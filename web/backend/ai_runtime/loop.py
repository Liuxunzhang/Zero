"""Explicit run → turn → message → tool state machine."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .context import ContextManager
from .models import (
    AssistantMessage,
    ProviderEvent,
    TextBlock,
    ThinkingSummaryBlock,
    ToolCallBlock,
    ToolResultMessage,
    TurnSnapshot,
    Usage,
)
from .providers.base import ContextOverflowError, ProviderAdapter, ProviderRequest, ProviderTransientError
from .storage import ConversationStore, ProviderStateStore
from .tools import ToolExecutionContext, ToolRegistry


Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
SnapshotFactory = Callable[[bool], TurnSnapshot]
AdapterFactory = Callable[[TurnSnapshot], ProviderAdapter]
EvidenceRecorder = Callable[[str, str, ToolResultMessage], Any]


_ACTIONABLE_UNCERTAINTY_MARKERS = (
    "无法确认",
    "不能确认",
    "尚无法",
    "需要补证",
    "需进一步验证",
    "建议进一步",
    "建议使用",
    "建议运行",
    "下一步",
)
_ACTIONABLE_TOOL_REFERENCE = re.compile(
    r"(?:linux|windows)\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+"
    r"|`?(?:list_plugins|run_plugin|describe_plugin)`?"
    r"|\b(?:malfind|pslist|psscan|pstree|bash|netstat|sockstat|sockscan|lsof|"
    r"dlllist|cmdline|vadinfo|envars|handles|maps)\b",
    re.IGNORECASE,
)


def requires_evidence_followup(assistant: AssistantMessage) -> bool:
    """Detect an Agent answer that deferred an executable verification."""

    text = "\n".join(
        block.text for block in assistant.content if isinstance(block, TextBlock)
    )
    return (
        any(marker in text for marker in _ACTIONABLE_UNCERTAINTY_MARKERS)
        and _ACTIONABLE_TOOL_REFERENCE.search(text) is not None
    )


class PartialTurnCancelled(asyncio.CancelledError):
    def __init__(self, assistant: AssistantMessage) -> None:
        super().__init__("model turn cancelled")
        self.assistant = assistant


class ModelTurnError(RuntimeError):
    def __init__(self, assistant: AssistantMessage, original: Exception) -> None:
        super().__init__(str(original))
        self.assistant = assistant
        self.original = original


@dataclass(slots=True)
class RunBudget:
    max_turns: int = 16
    max_tool_calls: int = 32
    max_seconds: int = 30 * 60
    max_output_continuations: int = 2
    turns_used: int = 0
    tool_calls_used: int = 0
    output_continuations_used: int = 0
    started_monotonic: float = 0.0
    final_summary_requested: bool = False

    def start(self) -> None:
        self.started_monotonic = self.started_monotonic or time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic) if self.started_monotonic else 0.0

    def exhausted(self) -> str:
        if self.turns_used >= self.max_turns:
            return "turn_budget"
        if self.tool_calls_used >= self.max_tool_calls:
            return "tool_budget"
        if self.elapsed_seconds >= self.max_seconds:
            return "time_budget"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_seconds": self.max_seconds,
            "max_output_continuations": self.max_output_continuations,
            "turns_used": self.turns_used,
            "tool_calls_used": self.tool_calls_used,
            "output_continuations_used": self.output_continuations_used,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "remaining_turns": max(0, self.max_turns - self.turns_used),
            "remaining_tool_calls": max(0, self.max_tool_calls - self.tool_calls_used),
            "remaining_output_continuations": max(
                0, self.max_output_continuations - self.output_continuations_used
            ),
            "remaining_seconds": max(0, round(self.max_seconds - self.elapsed_seconds, 3)),
        }


class AgentLoop:
    def __init__(
        self,
        *,
        conversations: ConversationStore,
        context: ContextManager,
        tools: ToolRegistry,
        adapter_factory: AdapterFactory,
        provider_states: ProviderStateStore | None = None,
        evidence_recorder: EvidenceRecorder | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.conversations = conversations
        self.context = context
        self.tools = tools
        self.adapter_factory = adapter_factory
        self.provider_states = provider_states
        self.evidence_recorder = evidence_recorder
        self.sleep = sleep

    async def run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        engine_id: str,
        image_id: str,
        snapshot_factory: SnapshotFactory,
        emit: Emit,
        cancel_event: asyncio.Event,
        budget: RunBudget,
        agent_mode: bool = True,
    ) -> dict[str, Any]:
        budget.start()
        await emit("run_start", {"budget": budget.to_dict()})
        final_reason = "completed"
        continue_truncated_output = False
        verification_followup = False
        verification_followups_used = 0
        try:
            while True:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                exhausted = budget.exhausted()
                final_phase = bool(exhausted)
                if final_phase and budget.final_summary_requested:
                    final_reason = exhausted
                    break
                if final_phase:
                    budget.final_summary_requested = True
                    await emit("budget_exhausted", {
                        "reason": exhausted,
                        "budget": budget.to_dict(),
                        "final_summary_turn": True,
                    })

                snapshot = snapshot_factory(final_phase or not agent_mode)
                turn_number = budget.turns_used + 1
                # The mandatory tools-disabled summary is a closure phase, not
                # another budgeted reasoning turn. Keep counters at their cap
                # instead of reporting confusing values such as 9/8.
                if not final_phase:
                    budget.turns_used = turn_number
                await emit("turn_start", {
                    "turn": turn_number,
                    "provider": snapshot.provider,
                    "protocol": snapshot.protocol,
                    "model": snapshot.model,
                    "tools_enabled": bool(snapshot.tools),
                    "budget": budget.to_dict(),
                })
                plan = await self.context.build(conversation_id, snapshot.context_window)
                await emit("context", plan.stats())
                if plan.compacted:
                    await emit("compaction", plan.stats())
                messages = list(plan.messages)
                if continue_truncated_output:
                    # The partial assistant message is already persisted and therefore
                    # present in ``messages``. This synthetic instruction is deliberately
                    # not added to conversation history.
                    from .models import UserMessage

                    messages.append(UserMessage(content=(
                        "上一条回答因达到输出上限而被截断。请严格从中断处继续，"
                        "不要重复任何已输出的标题、表格或段落；优先补完当前句子，"
                        "然后用最短篇幅收束剩余结论。"
                    )))
                    continue_truncated_output = False
                if verification_followup:
                    # Keep the superseded draft in model context so it knows
                    # which gaps it named, then force execution rather than
                    # another recommendation-only answer.
                    from .models import UserMessage

                    messages.append(UserMessage(content=(
                        "你刚才把当前工具可以核验的事项留在“无法确认/建议下一步”中，"
                        "但这是 Agent 模式，不是仅给建议的对话模式。该草稿不会作为最终答复展示。"
                        "现在立即调用工具补证：若尚未核验目录，先调用 list_plugins；"
                        "然后使用目录中的精确插件名执行相关只读插件。"
                        "不要再次让用户自行运行插件，也不要在未尝试工具前输出同一未决项。"
                        "只有插件不存在、工具实际失败/超时或运行预算耗尽时，"
                        "最终答复才可保留该项，并写明已尝试的工具和失败原因。"
                    )))
                    verification_followup = False
                if final_phase:
                    # Synthetic instruction is not persisted as user history.
                    from .models import UserMessage

                    reason_details = {
                        "turn_budget": (
                            f"模型轮次已用 {budget.max_turns}/{budget.max_turns}"
                        ),
                        "tool_budget": (
                            f"工具调用已用 {budget.tool_calls_used}/{budget.max_tool_calls}"
                        ),
                        "time_budget": (
                            f"运行时间已用 {budget.elapsed_seconds:.1f}/{budget.max_seconds} 秒"
                        ),
                    }
                    messages.append(UserMessage(content=(
                        f"运行预算已耗尽，具体原因：{reason_details.get(exhausted, exhausted)}。"
                        "禁止再调用工具；请基于已有证据输出最终总结："
                        "结论概览、已执行动作、关键证据及来源、假设、未决问题。"
                        "提及未执行动作时必须沿用上述具体预算原因，不得只写笼统的“预算耗尽”。"
                    )))
                try:
                    provider_state = (
                        self.provider_states.for_messages(
                            conversation_id,
                            [message.message_id for message in messages],
                            provider_family=snapshot.provider_family,
                            model=snapshot.model,
                        )
                        if self.provider_states
                        else {}
                    )
                    assistant, calls, continuation_state = await self._model_turn(
                        snapshot=snapshot,
                        messages=messages,
                        emit=emit,
                        provider_state=provider_state,
                        context_overflow_retry=lambda window=snapshot.context_window: self.context.compact(
                            conversation_id, window
                        ),
                    )
                except PartialTurnCancelled as exc:
                    self.conversations.append_message(conversation_id, exc.assistant)
                    for block in exc.assistant.content:
                        if isinstance(block, ToolCallBlock):
                            self.conversations.append_message(
                                conversation_id,
                                ToolResultMessage(
                                    block.tool_call_id,
                                    "模型请求在工具调用完成前被中止；工具未执行。",
                                    details={
                                        "tool_name": block.name,
                                        "tool_call_id": block.tool_call_id,
                                        "aborted": True,
                                    },
                                    is_error=True,
                                ),
                            )
                    raise asyncio.CancelledError
                except ModelTurnError as exc:
                    self.conversations.append_message(conversation_id, exc.assistant)
                    raise exc.original
                needs_verification = (
                    agent_mode
                    and not final_phase
                    and not calls
                    and bool(snapshot.tools)
                    and verification_followups_used < 2
                    and budget.tool_calls_used < budget.max_tool_calls
                    and budget.turns_used < budget.max_turns
                    and requires_evidence_followup(assistant)
                )
                if needs_verification:
                    assistant.status = "verification_required"
                self.conversations.append_message(conversation_id, assistant)
                if self.provider_states and calls and continuation_state:
                    self.provider_states.set(
                        conversation_id,
                        assistant.message_id,
                        provider_family=snapshot.provider_family,
                        model=snapshot.model,
                        state=continuation_state,
                    )
                await emit("message_end", {
                    "message": {
                        "message_id": assistant.message_id,
                        "stop_reason": assistant.stop_reason,
                        "status": assistant.status,
                    },
                    "usage": assistant.usage.to_dict(),
                })
                if needs_verification:
                    verification_followups_used += 1
                    verification_followup = True
                    discarded_chars = sum(
                        len(block.text)
                        for block in assistant.content
                        if isinstance(block, TextBlock)
                    )
                    await emit("verification_required", {
                        "attempt": verification_followups_used,
                        "max_attempts": 2,
                        "discarded_chars": discarded_chars,
                        "budget": budget.to_dict(),
                    })
                    await emit("turn_end", {
                        "turn": turn_number,
                        "stop_reason": "verification_required",
                        "continuing": True,
                        "budget": budget.to_dict(),
                    })
                    continue
                if (
                    assistant.stop_reason == "length"
                    and not calls
                    and not final_phase
                    and not budget.exhausted()
                    and budget.output_continuations_used < budget.max_output_continuations
                ):
                    budget.output_continuations_used += 1
                    continue_truncated_output = True
                    await emit("output_continuation", {
                        "attempt": budget.output_continuations_used,
                        "max_attempts": budget.max_output_continuations,
                        "reason": "length",
                        "budget": budget.to_dict(),
                    })
                    await emit("turn_end", {
                        "turn": turn_number,
                        "stop_reason": assistant.stop_reason,
                        "continuing": True,
                        "budget": budget.to_dict(),
                    })
                    continue
                if final_phase or not calls:
                    final_reason = exhausted or assistant.stop_reason or "completed"
                    await emit("turn_end", {
                        "turn": turn_number,
                        "stop_reason": assistant.stop_reason,
                        "budget": budget.to_dict(),
                    })
                    break

                post_turn_exhausted = budget.exhausted()
                allowed_count = (
                    0
                    if post_turn_exhausted
                    else max(0, budget.max_tool_calls - budget.tool_calls_used)
                )
                executable = calls[:allowed_count]
                if len(executable) < len(calls):
                    await emit("budget_exhausted", {
                        "reason": post_turn_exhausted or "tool_budget",
                        "skipped_tool_calls": len(calls) - len(executable),
                    })
                try:
                    results = await self._execute_tools(
                        executable,
                        run_id=run_id,
                        conversation_id=conversation_id,
                        engine_id=engine_id,
                        image_id=image_id,
                        cancel_event=cancel_event,
                        emit=emit,
                    )
                except asyncio.CancelledError:
                    for interrupted in executable:
                        self.conversations.append_message(
                            conversation_id,
                            ToolResultMessage(
                                interrupted.tool_call_id,
                                "工具执行已中止；非幂等操作不会自动重跑。",
                                details={
                                    "tool_name": interrupted.name,
                                    "tool_call_id": interrupted.tool_call_id,
                                    "aborted": True,
                                },
                                is_error=True,
                            ),
                        )
                    raise
                budget.tool_calls_used += len(executable)
                for result in results:
                    self.conversations.append_message(conversation_id, result)
                    if self.evidence_recorder and not result.is_error:
                        recorded = self.evidence_recorder(image_id, conversation_id, result)
                        if inspect.isawaitable(recorded):
                            await recorded
                for skipped in calls[len(executable):]:
                    result = ToolResultMessage(
                        skipped.tool_call_id,
                        "运行预算已耗尽，该工具调用未执行。请基于已有证据完成最终总结。",
                        details={
                            "tool_name": skipped.name,
                            "tool_call_id": skipped.tool_call_id,
                            "budget_exhausted": True,
                        },
                        is_error=True,
                    )
                    self.conversations.append_message(conversation_id, result)
                    await emit("tool_end", {
                        "tool_call_id": skipped.tool_call_id,
                        "tool_name": skipped.name,
                        "is_error": True,
                        "content": result.content,
                        "details": result.details,
                    })
                await emit("turn_end", {
                    "turn": turn_number,
                    "stop_reason": assistant.stop_reason,
                    "budget": budget.to_dict(),
                })
            return {"status": "completed", "reason": final_reason, "budget": budget.to_dict()}
        except asyncio.CancelledError:
            raise

    async def _model_turn(
        self,
        *,
        snapshot: TurnSnapshot,
        messages: list[Any],
        emit: Emit,
        provider_state: dict[str, dict[str, Any]],
        context_overflow_retry: Callable[[], Awaitable[Any]],
    ) -> tuple[AssistantMessage, list[ToolCallBlock], dict[str, Any]]:
        text_parts: list[str] = []
        summary_parts: list[str] = []
        calls: list[ToolCallBlock] = []
        usage = Usage()
        stop_reason = ""
        request_id = ""
        continuation_state: dict[str, Any] = {}
        overflow_retried = False
        attempts = 0
        while True:
            adapter = self.adapter_factory(snapshot)
            try:
                async for event in adapter.stream(ProviderRequest(
                    snapshot=snapshot,
                    messages=messages,
                    metadata={"provider_state": provider_state},
                )):
                    await self._forward_provider_event(event, emit)
                    if event.type == "text_delta":
                        text_parts.append(str(event.data.get("text", "")))
                    elif event.type == "thinking_summary_delta":
                        summary_parts.append(str(event.data.get("text", "")))
                    elif event.type == "tool_call":
                        calls.append(ToolCallBlock(
                            tool_call_id=str(event.data.get("tool_call_id", "")),
                            name=str(event.data.get("name", "")),
                            arguments=dict(event.data.get("arguments") or {}),
                            raw_arguments=str(event.data.get("raw_arguments", "")),
                            complete=bool(event.data.get("complete", True)),
                        ))
                    elif event.type == "provider_state":
                        continuation_state.update(dict(event.data or {}))
                    elif event.type == "usage":
                        usage = usage.merge(Usage.from_mapping(event.data))
                    elif event.type == "message_end":
                        stop_reason = str(event.data.get("stop_reason", ""))
                        request_id = str(event.data.get("request_id", ""))
                        usage = usage.merge(Usage.from_mapping(event.data.get("usage") or {}))
                break
            except asyncio.CancelledError:
                blocks = []
                if text_parts:
                    blocks.append(TextBlock(text="".join(text_parts)))
                if summary_parts:
                    blocks.append(ThinkingSummaryBlock(text="".join(summary_parts)))
                blocks.extend(calls)
                raise PartialTurnCancelled(AssistantMessage(
                    content=blocks,
                    stop_reason="cancelled",
                    provider=snapshot.provider,
                    model=snapshot.model,
                    request_id=request_id,
                    usage=usage,
                    status="aborted",
                ))
            except ContextOverflowError:
                if overflow_retried:
                    partial = AssistantMessage(
                        content=[TextBlock(text="".join(text_parts))] if text_parts else [],
                        stop_reason="context_overflow",
                        provider=snapshot.provider,
                        model=snapshot.model,
                        usage=usage,
                        status="error",
                    )
                    raise ModelTurnError(partial, ContextOverflowError("context overflow after compaction"))
                overflow_retried = True
                plan = await context_overflow_retry()
                messages = list(plan.messages)
                text_parts.clear()
                summary_parts.clear()
                calls.clear()
                continuation_state.clear()
                await emit("compaction", {**plan.stats(), "reason": "context_overflow_retry"})
            except ProviderTransientError as exc:
                if attempts >= 3:
                    partial = AssistantMessage(
                        content=[TextBlock(text="".join(text_parts))] if text_parts else [],
                        stop_reason="provider_error",
                        provider=snapshot.provider,
                        model=snapshot.model,
                        usage=usage,
                        status="error",
                    )
                    raise ModelTurnError(partial, exc)
                delay = exc.retry_after if exc.retry_after is not None else (2, 4, 8)[attempts]
                if delay > 60:
                    raise
                attempts += 1
                await emit("retry", {"attempt": attempts, "delay_seconds": delay, "error": str(exc)})
                await self.sleep(delay)
                text_parts.clear()
                summary_parts.clear()
                calls.clear()
                continuation_state.clear()
            except Exception as exc:
                partial = AssistantMessage(
                    content=[TextBlock(text="".join(text_parts))] if text_parts else [],
                    stop_reason="provider_error",
                    provider=snapshot.provider,
                    model=snapshot.model,
                    usage=usage,
                    status="error",
                )
                raise ModelTurnError(partial, exc)
            finally:
                await adapter.close()
        blocks = []
        if text_parts:
            blocks.append(TextBlock(text="".join(text_parts)))
        if summary_parts:
            blocks.append(ThinkingSummaryBlock(text="".join(summary_parts)))
        blocks.extend(calls)
        return AssistantMessage(
            content=blocks,
            stop_reason=stop_reason,
            provider=snapshot.provider,
            model=snapshot.model,
            request_id=request_id,
            usage=usage,
        ), calls, continuation_state

    @staticmethod
    async def _forward_provider_event(event: ProviderEvent, emit: Emit) -> None:
        mapping = {
            "text_delta": "text_delta",
            "thinking_summary_delta": "thinking_summary_delta",
            "tool_call_delta": "tool_call_delta",
            "tool_call": "tool_call",
            "usage": "usage",
            "message_start": "message_start",
        }
        if event.type in mapping:
            await emit(mapping[event.type], event.data)

    async def _execute_tools(
        self,
        calls: list[ToolCallBlock],
        *,
        run_id: str,
        conversation_id: str,
        engine_id: str,
        image_id: str,
        cancel_event: asyncio.Event,
        emit: Emit,
    ) -> list[ToolResultMessage]:
        results: list[ToolResultMessage] = []

        async def execute(call: ToolCallBlock) -> ToolResultMessage:
            await emit("tool_start", {
                "tool_call_id": call.tool_call_id,
                "tool_name": call.name,
                "arguments": call.arguments,
            })

            async def progress(data: dict[str, Any]) -> None:
                await emit("tool_progress", {
                    "tool_call_id": call.tool_call_id,
                    "tool_name": call.name,
                    **data,
                })

            result = await self.tools.execute(
                call.name,
                call.arguments,
                ToolExecutionContext(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    engine_id=engine_id,
                    image_id=image_id,
                    tool_call_id=call.tool_call_id,
                    progress=progress,
                    cancel_event=cancel_event,
                ),
                raw_arguments=call.raw_arguments,
                call_complete=call.complete,
            )
            await emit("tool_end", {
                "tool_call_id": call.tool_call_id,
                "tool_name": call.name,
                "is_error": result.is_error,
                "content": result.content,
                "details": result.details,
            })
            return result

        index = 0
        while index < len(calls):
            call = calls[index]
            definition = self.tools.get(call.name)
            if definition and definition.execution_mode == "parallel":
                batch = []
                while index < len(calls):
                    candidate = self.tools.get(calls[index].name)
                    if not candidate or candidate.execution_mode != "parallel":
                        break
                    batch.append(calls[index])
                    index += 1
                results.extend(await asyncio.gather(*(execute(item) for item in batch)))
            else:
                results.append(await execute(call))
                index += 1
        return results
