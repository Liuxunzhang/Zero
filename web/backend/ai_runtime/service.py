"""Composition root for the six runtime layers."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from zero.core.cache_key import image_identity

from .context import ContextManager, EvidenceStore
from .loop import AgentLoop, RunBudget
from .models import TurnSnapshot, UserMessage
from .models import (
    AssistantMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
)
from .providers import ADAPTERS
from .runs import RunManager
from .storage import ConversationStore, CredentialStore
from .tools import ResultHandleStore, ToolDefinition, ToolExecutionContext, ToolRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AI_ROOT = PROJECT_ROOT / ".zero" / "ai"

BUILTIN_MODEL_CATALOG = [
    {
        "name": "OpenAI GPT-5.6 Sol",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.6-sol",
        "protocol": "openai_responses",
        "context_window": 1_050_000,
        "reasoning_level": "medium",
    },
    {
        "name": "Anthropic Claude Opus 5",
        "base_url": "https://api.anthropic.com",
        "model": "claude-opus-5",
        "protocol": "anthropic_messages",
        "context_window": 1_000_000,
        "reasoning_level": "medium",
    },
    {
        "name": "Google Gemini 2.5 Flash",
        "base_url": "",
        "model": "gemini-2.5-flash",
        "protocol": "google_genai",
        "context_window": 1_048_576,
        "reasoning_level": "medium",
    },
    {
        "name": "DeepSeek V4 Pro",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "protocol": "openai_chat",
        "context_window": 1_000_000,
        "reasoning_level": "medium",
    },
]


def infer_protocol(profile: dict[str, Any]) -> str:
    explicit = str(profile.get("protocol") or "").strip()
    if explicit in ADAPTERS:
        return explicit
    combined = " ".join(str(profile.get(key, "")) for key in ("name", "base_url", "model")).lower()
    if "anthropic" in combined or "claude" in combined:
        return "anthropic_messages"
    if "google" in combined or "gemini" in combined:
        return "google_genai"
    if profile.get("use_responses_api"):
        return "openai_responses"
    return "openai_chat"


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(profile)
    normalized["protocol"] = infer_protocol(normalized)
    try:
        normalized["context_window"] = max(4096, int(normalized.get("context_window") or 65_536))
    except (TypeError, ValueError):
        normalized["context_window"] = 65_536
    normalized["context_window_estimated"] = "context_window" not in profile
    reasoning = str(normalized.get("reasoning_level") or "off").lower()
    normalized["reasoning_level"] = reasoning if reasoning in {"off", "low", "medium", "high"} else "off"
    capabilities = dict(normalized.get("capabilities") or {})
    capabilities.setdefault("tools", True)
    capabilities.setdefault("streaming", True)
    capabilities.setdefault("usage", True)
    capabilities.setdefault("reasoning", True)
    capabilities.setdefault("thinking_summary", normalized["protocol"] != "openai_chat")
    normalized["capabilities"] = capabilities
    return normalized


class AgentRuntime:
    def __init__(self, root: Path = AI_ROOT) -> None:
        self.root = Path(root)
        self._archive_global_memory()
        self.conversations = ConversationStore(self.root / "conversations")
        self.credentials = CredentialStore(self.root / "credentials.json")
        self.credentials.migrate_profiles(self.root / "profiles.json")
        self._migrate_config_credentials()
        self.context = ContextManager(self.conversations)
        self.evidence = EvidenceStore(self.root / "evidence")
        self.results = ResultHandleStore(self.root / "tool_results")
        self.tools = ToolRegistry()
        self._register_tools()
        self.runs = RunManager(self.root / "runs", cancel_external=self._cancel_engine)
        self._reconcile_interrupted_runs()
        self.loop = AgentLoop(
            conversations=self.conversations,
            context=self.context,
            tools=self.tools,
            adapter_factory=self._adapter,
            evidence_recorder=self._record_evidence,
        )

    def _archive_global_memory(self) -> None:
        """Legacy memory had no reliable image provenance and is never injected."""
        archive = self.root / "archive"
        for name in ("compressed_memory.json", "memory_items.json", "memory_stats.json"):
            source = self.root / name
            target = archive / f"{name}.bak-v1"
            if source.exists() and not target.exists():
                archive.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))

    def _migrate_config_credentials(self) -> None:
        from zero import config

        current = self.credentials.load()
        changed = False
        for profile in getattr(config, "AI_PROFILES", []) or []:
            if not isinstance(profile, dict):
                continue
            profile_id = str(profile.get("id") or "")
            api_key = str(profile.get("api_key") or "")
            if profile_id and api_key and profile_id not in current:
                current[profile_id] = api_key
                changed = True
        config_key = str(getattr(config, "AI_API_KEY", "") or "")
        if config_key and "config" not in current:
            current["config"] = config_key
            changed = True
        if changed:
            self.credentials.save(current)

    def _reconcile_interrupted_runs(self) -> None:
        """Materialize crash-interrupted model/tool state without replaying it."""
        for run in self.runs._load_index():
            if run.get("status") != "interrupted":
                continue
            run_id = str(run.get("run_id") or "")
            conversation_id = str(run.get("conversation_id") or "")
            if not run_id or not self.conversations.get(conversation_id):
                continue
            entries = self.conversations.entries(conversation_id)
            if any(
                entry.get("type") == "run_recovery" and entry.get("run_id") == run_id
                for entry in entries
            ):
                continue
            events = self.runs.events.read(run_id)
            last_turn = max(
                (int(event.get("seq", 0)) for event in events if event.get("type") == "turn_start"),
                default=0,
            )
            completed_message = any(
                event.get("type") == "message_end"
                and int(event.get("seq", 0)) > last_turn
                for event in events
            )
            if last_turn and not completed_message:
                partial_text = "".join(
                    str((event.get("data") or {}).get("text") or "")
                    for event in events
                    if event.get("type") == "text_delta"
                    and int(event.get("seq", 0)) > last_turn
                )
                if partial_text:
                    turn_event = next(
                        (
                            event for event in reversed(events)
                            if event.get("type") == "turn_start"
                        ),
                        {},
                    )
                    turn_data = turn_event.get("data") or {}
                    self.conversations.append_message(
                        conversation_id,
                        AssistantMessage(
                            content=[TextBlock(text=partial_text)],
                            stop_reason="interrupted",
                            provider=str(turn_data.get("protocol") or ""),
                            model=str(turn_data.get("model") or ""),
                            status="interrupted",
                        ),
                    )

            messages = self.conversations.messages(conversation_id)
            completed_calls = {
                message.tool_call_id
                for message in messages
                if isinstance(message, ToolResultMessage)
            }
            for message in messages:
                if not isinstance(message, AssistantMessage):
                    continue
                for block in message.content:
                    if isinstance(block, ToolCallBlock) and block.tool_call_id not in completed_calls:
                        self.conversations.append_message(
                            conversation_id,
                            ToolResultMessage(
                                block.tool_call_id,
                                "服务重启时工具仍未完成；该操作已标记 interrupted，未自动重跑。",
                                details={
                                    "tool_name": block.name,
                                    "tool_call_id": block.tool_call_id,
                                    "interrupted": True,
                                },
                                is_error=True,
                            ),
                        )
                        completed_calls.add(block.tool_call_id)
            self.conversations.append_entry(
                conversation_id,
                "run_recovery",
                {"run_id": run_id, "status": "interrupted"},
            )

    @staticmethod
    def _engine_status(engine_id: str) -> dict[str, Any]:
        from web.backend.services.vol_service import get_service

        try:
            return get_service().get_image_status(engine_id)
        except Exception:
            return {"loaded": False, "path": None, "os_family": "linux"}

    def image_id(self, engine_id: str) -> str:
        status = self._engine_status(engine_id)
        return image_identity(str(status.get("path") or "")) if status.get("path") else ""

    @staticmethod
    def _cancel_engine(engine_id: str) -> bool:
        from web.backend.services.vol_service import get_service

        try:
            return get_service().cancel_plugin(engine_id)
        except Exception:
            return False

    def active_profile(self) -> dict[str, Any]:
        from web.backend.services.ai_service import get_ai_service

        ai = get_ai_service()
        profile = ai.get_active_profile()
        if profile:
            raw = dict(profile)
        else:
            cfg = ai.get_config_info()
            raw = {
                "id": "config",
                "name": cfg.get("provider", "config.py"),
                "base_url": cfg.get("base_url", ""),
                "model": cfg.get("model", ""),
                "context_window": 65_536,
                "context_window_estimated": True,
            }
        normalized = normalize_profile(raw)
        profile_id = str(normalized.get("id") or "config")
        key = self.credentials.load().get(profile_id)
        if not key:
            key = str(raw.get("api_key") or "")
        if not key:
            try:
                key = ai._resolve_config()[1]  # compatibility with config.py
            except Exception:
                key = ""
        normalized["api_key"] = "" if key == "sk-placeholder" else key
        return normalized

    def _snapshot(self, tools_disabled: bool = False) -> TurnSnapshot:
        from web.backend.services.ai_service import get_ai_service

        profile = self.active_profile()
        ai = get_ai_service()
        settings = ai.get_ai_settings()
        max_tokens = settings.get("ai_max_tokens", 4096)
        try:
            max_output = max(1, int(max_tokens))
        except (TypeError, ValueError):
            max_output = 4096
        capabilities = dict(profile.get("capabilities") or {})
        declarations = (
            []
            if tools_disabled or capabilities.get("tools") is False
            else self.tools.declarations()
        )
        reasoning_level = (
            profile["reasoning_level"]
            if capabilities.get("reasoning", True)
            else "off"
        )
        return TurnSnapshot.create(
            provider=profile["protocol"],
            model=str(profile.get("model") or ""),
            protocol=profile["protocol"],
            system_prompt=ai._resolve_system_prompt(),
            reasoning_level=reasoning_level,
            max_output_tokens=max_output,
            tools=declarations,
            context_window=profile["context_window"],
            thinking_summary=capabilities.get("thinking_summary", True),
        )

    def _adapter(self, snapshot: TurnSnapshot):
        profile = self.active_profile()
        adapter_type = ADAPTERS[snapshot.protocol]
        return adapter_type(
            api_key=str(profile.get("api_key") or ""),
            base_url=str(profile.get("base_url") or ""),
        )

    def create_conversation(self, *, title: str = "新对话", engine_id: str = "vol3") -> dict[str, Any]:
        return self.conversations.create(
            title=title,
            engine_id=engine_id,
            image_id=self.image_id(engine_id),
        )

    def create_run(
        self,
        conversation_id: str,
        *,
        message: str,
        engine_id: str = "vol3",
        mode: str = "agent",
        include_context: bool = True,
        max_turns: int = 12,
        max_tool_calls: int = 20,
        max_seconds: int = 1800,
    ) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("message cannot be empty")
        image_id = self.image_id(engine_id)
        self.conversations.ensure_writable(conversation_id, engine_id, image_id)
        forensic_context = self._current_plugin_context(engine_id) if include_context else ""
        self.conversations.append_message(
            conversation_id,
            UserMessage(content=message.strip(), context=forensic_context),
        )
        budget = RunBudget(
            max_turns=max(1, min(100, int(max_turns))),
            max_tool_calls=max(0, min(200, int(max_tool_calls))),
            max_seconds=max(1, min(24 * 3600, int(max_seconds))),
        )

        async def execute(emit, cancel_event):
            try:
                return await self.loop.run(
                    run_id=record.run_id,
                    conversation_id=conversation_id,
                    engine_id=engine_id,
                    image_id=image_id,
                    snapshot_factory=self._snapshot,
                    emit=emit,
                    cancel_event=cancel_event,
                    budget=budget,
                    agent_mode=mode == "agent",
                )
            finally:
                self.conversations.update_meta(conversation_id, active_run_id=None)

        record = self.runs.create(
            conversation_id=conversation_id,
            engine_id=engine_id,
            image_id=image_id,
            execute=execute,
            budget=budget.to_dict(),
        )
        self.conversations.update_meta(conversation_id, active_run_id=record.run_id)
        return record.public()

    @staticmethod
    def _current_plugin_context(engine_id: str) -> str:
        from web.backend.services.ai_service import _format_plugin_context, get_ai_service
        from web.backend.services.vol_service import get_service

        if not AgentRuntime._engine_status(engine_id).get("loaded"):
            return ""
        settings = get_ai_service().get_ai_settings()
        limit = settings.get("ai_context_max_rows", 200)
        try:
            if isinstance(limit, str) and limit.lower() == "max":
                payload = get_service().get_current_result_context(engine_id=engine_id)
            else:
                payload = get_service().get_current_result_context(
                    max_rows=max(1, int(limit)),
                    engine_id=engine_id,
                )
        except (TypeError, ValueError):
            try:
                payload = get_service().get_current_result_context(
                    max_rows=200, engine_id=engine_id
                )
            except Exception:
                return ""
        except Exception:
            return ""
        if not payload:
            return ""
        payload = dict(payload)
        payload["_max_rows"] = limit
        payload["_max_chars"] = settings.get("ai_context_max_chars", 50_000)
        return _format_plugin_context(payload)

    def context_stats(self, conversation_id: str) -> dict[str, Any]:
        profile = self.active_profile()
        plan = self.context.inspect(conversation_id, profile["context_window"])
        return {
            **plan.stats(),
            "conversation_id": conversation_id,
            "image_id": (self.conversations.get(conversation_id) or {}).get("image_id", ""),
            "message_count": len(plan.messages),
            "checkpoint_count": len(self.conversations.checkpoints(conversation_id)),
            "context_window_estimated": profile.get("context_window_estimated", False),
        }

    async def compact(self, conversation_id: str) -> dict[str, Any]:
        profile = self.active_profile()
        plan = await self.context.compact(conversation_id, profile["context_window"])
        return plan.stats()

    def public_profiles(self, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for raw in profiles:
            profile = normalize_profile(raw)
            profile.pop("api_key", None)
            profile.update(self.credentials.masked(str(profile.get("id") or "")))
            result.append(profile)
        return result

    def _record_evidence(
        self,
        image_id: str,
        conversation_id: str,
        result,
    ) -> None:
        source = {
            key: result.details.get(key)
            for key in ("plugin", "result_id", "tool_call_id")
            if result.details.get(key)
        }
        if source:
            self.evidence.add(
                image_id or "unknown",
                conversation_id,
                result.content[:2000],
                source,
            )

    def _register_tools(self) -> None:
        object_schema = {"type": "object", "properties": {}, "additionalProperties": False}
        self.tools.register(ToolDefinition(
            "get_image_status",
            "Return the current forensic image identity and engine status.",
            object_schema,
            self._tool_image_status,
            execution_mode="parallel",
        ))
        self.tools.register(ToolDefinition(
            "list_plugins",
            "List safe installed Volatility plugins for the current image OS.",
            object_schema,
            lambda arguments, context: self._legacy_named("list_plugins", arguments, context),
        ))
        self.tools.register(ToolDefinition(
            "describe_plugin",
            "Describe one installed plugin and its allowed arguments.",
            {
                "type": "object",
                "properties": {"plugin_name": {"type": "string", "minLength": 1}},
                "required": ["plugin_name"],
                "additionalProperties": False,
            },
            self._tool_describe_plugin,
            execution_mode="parallel",
        ))
        self.tools.register(ToolDefinition(
            "run_plugin",
            "Run one allowlisted Volatility plugin. Heavy scanners require pid/pids.",
            {
                "type": "object",
                "properties": {
                    "plugin_name": {"type": "string", "minLength": 1},
                    "args": {"type": "object"},
                    "pid": {"type": ["integer", "string"]},
                    "pids": {"type": "array", "items": {"type": ["integer", "string"]}},
                },
                "required": ["plugin_name"],
                "additionalProperties": True,
            },
            self._tool_run_plugin,
            timeout_seconds=1800,
        ))
        self.tools.register(ToolDefinition(
            "query_plugin_result",
            "Filter, sort, select columns and paginate an opaque plugin result handle.",
            {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string", "pattern": "^res_"},
                    "filter": {"type": "string"},
                    "sort_column": {"type": "string"},
                    "sort_desc": {"type": "boolean"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["result_id"],
                "additionalProperties": False,
            },
            self._tool_query,
            execution_mode="parallel",
        ))
        dump_common = {
            "type": "object",
            "properties": {
                "pid": {"type": ["integer", "string"]},
                "process_name": {"type": "string"},
                "max_matches": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
            "anyOf": [{"required": ["pid"]}, {"required": ["process_name"]}],
        }
        self.tools.register(ToolDefinition(
            "dump_process", "Dump a process into the restricted AI dump directory.", dump_common,
            lambda arguments, context: self._legacy_named("dump_process", arguments, context),
            risk="extract", idempotent=False, timeout_seconds=1800,
        ))
        self.tools.register(ToolDefinition(
            "dump_pe",
            "Dump a Windows PE into the restricted AI dump directory.",
            {
                "type": "object",
                "properties": {
                    "pid": {"type": ["integer", "string"]},
                    "process_name": {"type": "string"},
                    "module_name": {"type": "string"},
                    "base": {"type": ["integer", "string"]},
                    "kernel_module": {"type": "boolean"},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "additionalProperties": False,
                "anyOf": [{"required": ["base"]}, {"required": ["module_name"]}, {"required": ["process_name"]}],
            },
            lambda arguments, context: self._legacy_named("dump_pe", arguments, context),
            risk="extract", idempotent=False, timeout_seconds=1800,
        ))

    async def _tool_image_status(self, _arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        status = self._engine_status(context.engine_id)
        return {**status, "engine_id": context.engine_id, "image_id": context.image_id}

    async def _legacy_named(self, name: str, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        from web.backend.api.ai_routes import _execute_agent_tool

        status = self._engine_status(context.engine_id)
        event_loop = asyncio.get_running_loop()

        def progress(value: Any) -> None:
            data = value if isinstance(value, dict) else {"message": str(value)}
            asyncio.run_coroutine_threadsafe(
                context.report(stage="running", **data),
                event_loop,
            )

        await context.report(stage="started", message=f"执行 {name}")
        result = await _execute_agent_tool(
            name,
            arguments,
            context.engine_id,
            os_family=str(status.get("os_family") or "linux"),
            progress_callback=progress,
        )
        await context.report(stage="completed", total=result.get("total", 0))
        return result

    async def _tool_describe_plugin(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        from web.backend.services.vol_service import get_service

        plugin = str(arguments["plugin_name"])
        service = get_service()
        return {
            "plugin": plugin,
            "metadata": service.get_plugin_metadata(plugin, context.engine_id) or {},
            "docs": service.get_plugin_docs(plugin, context.engine_id) or {},
        }

    async def _tool_run_plugin(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        result = await self._legacy_named("run_plugin", arguments, context)
        if result.get("status") == "pid_required":
            return result
        # Fetch the complete current result after the legacy safe executor has
        # resolved/validated and run it. This avoids injecting all rows into context.
        from web.backend.services.vol_service import get_service

        total = int(result.get("total", 0) or 0)
        complete = get_service().get_results(
            page=1,
            page_size=max(1, total),
            engine_id=context.engine_id,
        )
        handle = self.results.save(
            context.image_id,
            context.conversation_id,
            plugin=str(result.get("plugin") or complete.get("current_plugin") or ""),
            columns=list(complete.get("columns") or result.get("columns") or []),
            rows=list(complete.get("rows") or result.get("rows") or []),
            metadata={"arguments": result.get("arguments", {})},
        )
        handle["total"] = total or handle["total"]
        return handle

    async def _tool_query(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        return self.results.query(
            context.image_id,
            context.conversation_id,
            arguments["result_id"],
            filter_text=arguments.get("filter", ""),
            sort_column=arguments.get("sort_column", ""),
            sort_desc=bool(arguments.get("sort_desc", False)),
            columns=arguments.get("columns"),
            page=arguments.get("page", 1),
            page_size=arguments.get("page_size", 200),
        )


_runtime: AgentRuntime | None = None


def get_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime()
    return _runtime
