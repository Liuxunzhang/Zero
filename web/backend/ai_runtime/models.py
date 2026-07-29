"""Canonical messages and events shared by every runtime layer."""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TypeAlias


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def merge(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cache_read_tokens=max(self.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens=max(self.cache_write_tokens, other.cache_write_tokens),
            reasoning_tokens=max(self.reasoning_tokens, other.reasoning_tokens),
        )

    @classmethod
    def from_mapping(cls, value: Any) -> "Usage":
        def number(*names: str) -> int:
            for name in names:
                raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
                if raw is not None:
                    try:
                        return max(0, int(raw))
                    except (TypeError, ValueError):
                        pass
            return 0

        details = (
            value.get("output_tokens_details", {})
            if isinstance(value, dict)
            else getattr(value, "output_tokens_details", None) or {}
        )
        reasoning = (
            details.get("reasoning_tokens", 0)
            if isinstance(details, dict)
            else getattr(details, "reasoning_tokens", 0)
        )
        input_details = (
            value.get("input_tokens_details") or value.get("prompt_tokens_details") or {}
            if isinstance(value, dict)
            else (
                getattr(value, "input_tokens_details", None)
                or getattr(value, "prompt_tokens_details", None)
                or {}
            )
        )
        cached = (
            input_details.get("cached_tokens", 0)
            if isinstance(input_details, dict)
            else getattr(input_details, "cached_tokens", 0)
        )
        return cls(
            input_tokens=number("input_tokens", "prompt_tokens"),
            output_tokens=number("output_tokens", "completion_tokens"),
            cache_read_tokens=max(number("cache_read_tokens", "cached_tokens"), int(cached or 0)),
            cache_write_tokens=number("cache_write_tokens", "cache_creation_input_tokens"),
            reasoning_tokens=max(number("reasoning_tokens", "thoughts_token_count"), int(reasoning or 0)),
        )

    def to_dict(self) -> dict[str, int]:
        return {**asdict(self), "total_tokens": self.total_tokens}


@dataclass(slots=True, frozen=True)
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(slots=True, frozen=True)
class ThinkingSummaryBlock:
    type: Literal["thinking_summary"] = "thinking_summary"
    text: str = ""


@dataclass(slots=True, frozen=True)
class ToolCallBlock:
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    complete: bool = True


ContentBlock: TypeAlias = TextBlock | ThinkingSummaryBlock | ToolCallBlock


@dataclass(slots=True)
class UserMessage:
    content: str
    context: str = ""
    role: Literal["user"] = "user"
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=now_iso)


@dataclass(slots=True)
class AssistantMessage:
    content: list[ContentBlock] = field(default_factory=list)
    role: Literal["assistant"] = "assistant"
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=now_iso)
    stop_reason: str = ""
    provider: str = ""
    model: str = ""
    request_id: str = ""
    usage: Usage = field(default_factory=Usage)
    status: Literal[
        "complete",
        "aborted",
        "interrupted",
        "error",
        "verification_required",
    ] = "complete"


@dataclass(slots=True)
class ToolResultMessage:
    tool_call_id: str
    content: str
    details: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    role: Literal["tool_result"] = "tool_result"
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=now_iso)


Message: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage


@dataclass(slots=True)
class ProviderEvent:
    """One normalized provider stream event.

    Adapters never expose SDK objects.  ``data`` is JSON-serializable and
    provider signatures are intentionally absent.
    """

    type: Literal[
        "message_start",
        "text_delta",
        "thinking_summary_delta",
        "tool_call_delta",
        "tool_call",
        "provider_state",
        "usage",
        "message_end",
        "error",
    ]
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TurnSnapshot:
    provider: str
    model: str
    protocol: str
    system_prompt: str
    reasoning_level: str
    max_output_tokens: int
    tools: tuple[dict[str, Any], ...]
    context_window: int
    thinking_summary: bool = True
    provider_family: str = ""
    temperature: float | None = None

    @classmethod
    def create(cls, **values: Any) -> "TurnSnapshot":
        values["tools"] = tuple(copy.deepcopy(values.get("tools") or []))
        return cls(**values)


def block_to_dict(block: ContentBlock) -> dict[str, Any]:
    return asdict(block)


def message_to_dict(message: Message) -> dict[str, Any]:
    result = asdict(message)
    if isinstance(message, AssistantMessage):
        result["content"] = [block_to_dict(block) for block in message.content]
        result["usage"] = message.usage.to_dict()
    return result


def message_from_dict(raw: dict[str, Any]) -> Message:
    role = raw.get("role")
    common = {
        "message_id": raw.get("message_id") or raw.get("entry_id") or uuid.uuid4().hex,
        "created_at": raw.get("created_at") or raw.get("ts") or now_iso(),
    }
    if role == "user":
        return UserMessage(
            content=str(raw.get("content", "")),
            context=str(raw.get("context", "")),
            **common,
        )
    if role in {"tool", "tool_result"}:
        return ToolResultMessage(
            tool_call_id=str(raw.get("tool_call_id", "")),
            content=str(raw.get("content") or raw.get("toolSummary") or raw.get("toolError") or ""),
            details=dict(raw.get("details") or {}),
            is_error=bool(raw.get("is_error") or raw.get("toolError")),
            **common,
        )
    blocks: list[ContentBlock] = []
    content = raw.get("content", [])
    if isinstance(content, str):
        blocks.append(TextBlock(text=content))
    else:
        for block in content or []:
            kind = block.get("type") if isinstance(block, dict) else ""
            if kind == "tool_call":
                blocks.append(ToolCallBlock(
                    tool_call_id=str(block.get("tool_call_id", "")),
                    name=str(block.get("name", "")),
                    arguments=dict(block.get("arguments") or {}),
                    raw_arguments=str(block.get("raw_arguments", "")),
                    complete=bool(block.get("complete", True)),
                ))
            elif kind == "thinking_summary":
                blocks.append(ThinkingSummaryBlock(text=str(block.get("text", ""))))
            else:
                blocks.append(TextBlock(text=str(block.get("text", ""))))
    return AssistantMessage(
        content=blocks,
        stop_reason=str(raw.get("stop_reason", "")),
        provider=str(raw.get("provider", "")),
        model=str(raw.get("model", "")),
        request_id=str(raw.get("request_id", "")),
        usage=Usage.from_mapping(raw.get("usage") or {}),
        status=raw.get("status", "complete"),
        **common,
    )


def visible_text(message: Message) -> str:
    if isinstance(message, UserMessage):
        return message.content
    if isinstance(message, ToolResultMessage):
        return message.content
    return "".join(block.text for block in message.content if isinstance(block, TextBlock))
