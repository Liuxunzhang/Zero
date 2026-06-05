"""AI analysis SSE streaming routes + profile / prompt management."""

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from web.backend.services.ai_service import get_ai_service, AGENT_ALLOWED_PLUGINS, AGENT_PLUGIN_DESCRIPTIONS, AGENT_HEAVY_PLUGINS
from web.backend.services.vol_service import get_service
from web.backend.services import conversation_store as conv_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai")


# ── Request models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    include_context: bool = True
    engine: str = "vol3"
    conversation_id: Optional[str] = None   # if None, auto-create a new conversation
    mode: str = "agent"   # "chat" (对话模式) or "agent" (智能体模式)


class ProfileModel(BaseModel):
    id: str = ""
    name: str
    base_url: str
    api_key: str = ""
    model: str


class SaveProfilesRequest(BaseModel):
    profiles: list[ProfileModel]


class SetActiveProfileRequest(BaseModel):
    profile: Optional[ProfileModel] = None   # None = use config.py


class PromptModel(BaseModel):
    id: str = ""
    name: str
    content: str


class SetActivePromptRequest(BaseModel):
    prompt_id: str


class PersistConfigRequest(BaseModel):
    persist_to_config_py: bool


class AiSettingsRequest(BaseModel):
    ai_max_tokens: int | str | None = None
    ai_temperature: float | None = None
    ai_max_history: int | None = None
    ai_context_max_rows: int | str | None = None
    ai_context_max_chars: int | None = None
    ai_memory_enabled: bool | None = None
    ai_memory_max_chars: int | None = None
    ai_memory_retrieval_top_k: int | None = None
    ai_memory_retrieval_max_chars: int | None = None
    ai_memory_ttl_days: int | None = None


# ── Agent tool executor ─────────────────────────────────────────────

async def _execute_agent_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    engine_id: str,
    os_family: str = "linux",
) -> dict[str, Any]:
    """Execute a tool requested by the AI agent.

    *os_family* should reflect the **actual** OS of the loaded image
    (``"windows"`` or ``"linux"``) so that ``list_plugins`` defaults
    correctly and ``run_plugin`` can prepend the right prefix.
    """
    svc = get_service()
    mgr = svc._manager
    loop = asyncio.get_event_loop()

    # ── list_plugins ──────────────────────────────────────────────
    if tool_name == "list_plugins":
        # Always use the image's actual OS — ignore whatever the AI guesses.
        categories = await loop.run_in_executor(
            None,
            lambda: mgr.list_plugins(engine_id, os_family),
        )
        flat: list[str] = []
        for cat, plugins in (categories or {}).items():
            plugin_strs = []
            for p in plugins:
                full_p = p if p.startswith(f"{os_family}.") else f"{os_family}.{p}"
                desc = AGENT_PLUGIN_DESCRIPTIONS.get(full_p, "")
                if desc:
                    plugin_strs.append(f"{p} ({desc})")
                else:
                    plugin_strs.append(p)
            flat.append(f"[{cat}] {', '.join(plugin_strs)}")
        total = sum(len(v) for v in (categories or {}).values())
        return {
            "os_family": os_family,
            "categories": categories,
            "summary": (
                f"可用 {os_family} 插件共 {total} 个：\n" + "\n".join(flat)
            ),
        }

    # ── run_plugin ────────────────────────────────────────────────
    if tool_name == "run_plugin":
        plugin_name = str(tool_args.get("plugin_name", ""))
        if not plugin_name:
            raise ValueError("plugin_name is required")

        # Resolve plugin name via engine resolver first.
        engine = mgr.get_engine(engine_id)
        resolved_name = engine.resolve_plugin_name(plugin_name)

        # Prepend OS prefix if resolution was not fully resolved
        if resolved_name == plugin_name or resolved_name not in AGENT_ALLOWED_PLUGINS:
            test_name = plugin_name
            if "." in test_name and not test_name.startswith(("linux.", "windows.", "mac.")):
                test_name = f"{os_family}.{test_name}"
            elif "." not in test_name:
                test_name = f"{os_family}.{test_name}"
            resolved_name = engine.resolve_plugin_name(test_name)

        # Enforce read-only analysis plugin allowlist.
        if resolved_name not in AGENT_ALLOWED_PLUGINS:
            raise ValueError(f"Plugin '{resolved_name}' is not allowed for AI Agent execution.")

        plugin_name = resolved_name

        # Build kwargs – only forward non-None primitive values.
        kwargs: dict[str, Any] = {}
        pid = tool_args.get("pid")

        # Reject heavy memory-scanner plugins called without a pid —
        # they will stall scanning every process and hit the timeout.
        short = plugin_name.split(".", 1)[1] if "." in plugin_name else plugin_name
        if plugin_name in AGENT_HEAVY_PLUGINS and pid is None:
            raise ValueError(
                f"插件 {short} 是全量内存扫描类插件，不带 pid 参数会扫描所有进程导致超时。"
                f"请先通过 pslist/psscan 获取进程列表，分析出可疑 PID 后，"
                f"再调用 run_plugin(plugin_name=\"{short}\", pid=<可疑PID>)。"
            )

        if pid is not None:
            kwargs["pid"] = int(pid)

        def _run() -> tuple:
            return mgr.run_plugin(engine_id, plugin_name, **kwargs)

        columns, rows = await loop.run_in_executor(None, _run)
        total = len(rows)
        preview = [list(r) for r in rows[:100]]
        return {
            "plugin": plugin_name,
            "columns": columns,
            "rows": preview,
            "total": total,
            "truncated": total > 100,
            "summary": (
                f"插件 {plugin_name} 返回 {total} 行, "
                f"{len(columns)} 列 ({', '.join(columns[:20])})"
                + (f"（仅展示前 100 行）" if total > 100 else "")
            ),
        }

    raise ValueError(f"Unknown tool: {tool_name}")


# ── SSE streaming ──────────────────────────────────────────────────

async def _sse_stream(
    user_message: str,
    plugin_context: Optional[dict],
    conversation_id: str,
    tool_executor: Any = None,
    engine_id: str = "vol3",
    os_family: str = "linux",
):
    ai = get_ai_service()
    assistant_parts: list[str] = []
    messages_to_persist = []
    messages_to_persist.append({"role": "user", "content": user_message})
    try:
        async for event in ai.chat_stream(
            user_message,
            plugin_context,
            tool_executor=tool_executor,
            engine_id=engine_id,
            os_family=os_family,
            conversation_id=conversation_id,
        ):
            payload = json.dumps(event, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            
            ev_type = event.get("type")
            if ev_type == "chunk":
                assistant_parts.append(event.get("content", ""))
            elif ev_type == "tool_call":
                messages_to_persist.append({
                    "role": "tool",
                    "tool_call_id": event.get("tool_call_id"),
                    "toolName": event.get("tool_name"),
                    "toolArgs": event.get("arguments") or {},
                    "toolRunning": False,
                    "toolSummary": "",
                    "toolError": "",
                })
            elif ev_type == "tool_result":
                tc_id = event.get("tool_call_id")
                for msg in messages_to_persist:
                    if msg.get("role") == "tool" and msg.get("tool_call_id") == tc_id:
                        if event.get("ok"):
                            msg["toolSummary"] = event.get("summary") or ""
                        else:
                            msg["toolError"] = event.get("error") or "Unknown error"
                        break
        # Persist all turns to the conversation store.
        assistant_content = "".join(assistant_parts)
        if assistant_content:
            messages_to_persist.append({"role": "assistant", "content": assistant_content})
        
        conv_store.append_messages(conversation_id, messages_to_persist)
        yield (
            f"data: {json.dumps({'type': 'done', 'content': '', 'conversation_id': conversation_id})}\n\n"
        )
    except Exception as e:
        logger.error("SSE stream error: %s", e, exc_info=True)
        payload = json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False)
        yield f"data: {payload}\n\n"


@router.post("/chat")
async def ai_chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Message cannot be empty")

    ai = get_ai_service()
    cfg = ai.get_config_info()
    if not cfg["has_api_key"]:
        raise HTTPException(
            400,
            "未配置 AI API Key。请通过设置面板或 config.py 进行配置。",
        )

    plugin_context = None
    svc = get_service()
    if req.include_context:
        ai_settings = cfg.get("ai_settings", {}) if isinstance(cfg, dict) else {}
        limit = ai_settings.get("ai_context_max_rows", 200)
        engine_id = str(req.engine or "vol3")
        if isinstance(limit, str) and limit.strip().lower() == "max":
            plugin_context = svc.get_current_result_context(engine_id=engine_id)
        else:
            try:
                plugin_context = svc.get_current_result_context(
                    max_rows=int(limit), engine_id=engine_id
                )
            except (TypeError, ValueError):
                plugin_context = svc.get_current_result_context(max_rows=200, engine_id=engine_id)

    # Resolve or create the conversation to save into.
    conv_id = req.conversation_id
    if conv_id and not conv_store.get_conversation(conv_id):
        conv_id = None          # stale id — treat as new
    if not conv_id:
        meta = conv_store.create_conversation(engine=req.engine)
        conv_id = meta["id"]

    engine_id = str(req.engine or "vol3")

    # Detect the loaded image's OS family so the agent and system prompt
    # know whether to use linux.* or windows.* plugins.
    try:
        img_status = svc._manager.get_image_status(engine_id)
    except Exception:
        img_status = {}
    os_family = str(img_status.get("os_family", "linux") or "linux")

    # Build the agent tool executor (lazy — only invoked when the AI
    # actually calls a tool).
    async def _tool_executor(
        tool_name: str,
        tool_args: dict[str, Any],
        _engine_id: str,
    ) -> dict[str, Any]:
        return await _execute_agent_tool(tool_name, tool_args, _engine_id, os_family=os_family)

    tool_exec = _tool_executor if req.mode == "agent" else None

    return StreamingResponse(
        _sse_stream(
            message,
            plugin_context,
            conv_id,
            tool_executor=tool_exec,
            engine_id=engine_id,
            os_family=os_family,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── History ────────────────────────────────────────────────────────

@router.get("/history")
async def ai_history(conversation_id: Optional[str] = None):
    return {"history": get_ai_service().get_history(conversation_id)}


@router.delete("/history")
async def clear_ai_history(conversation_id: Optional[str] = None):
    get_ai_service().clear_history(conversation_id)
    return {"ok": True}


@router.delete("/memory")
async def clear_ai_memory():
    removed = get_ai_service().clear_all_memory()
    return {"ok": True, "removed_items": removed}


@router.get("/memory")
async def get_ai_memory():
    return {"compressed_memory": get_ai_service().get_compressed_memory()}


@router.get("/memory/stats")
async def get_ai_memory_stats():
    return {"stats": get_ai_service().get_memory_stats()}


@router.get("/config")
async def ai_config():
    return get_ai_service().get_config_info()


@router.get("/persist-config")
async def get_persist_config():
    return {"persist_to_config_py": get_ai_service().get_persist_to_config()}


@router.post("/persist-config")
async def set_persist_config(req: PersistConfigRequest):
    enabled = get_ai_service().set_persist_to_config(req.persist_to_config_py)
    return {"ok": True, "persist_to_config_py": enabled}


@router.get("/settings")
async def get_ai_settings():
    return {"settings": get_ai_service().get_ai_settings()}


@router.post("/settings")
async def save_ai_settings(req: AiSettingsRequest):
    payload = req.model_dump(exclude_none=True)
    settings = get_ai_service().save_ai_settings(payload)
    return {"ok": True, "settings": settings}


# ── Profiles ───────────────────────────────────────────────────────

@router.get("/profiles")
async def list_profiles():
    return {"profiles": get_ai_service().get_profiles()}


@router.post("/profiles")
async def save_profiles(req: SaveProfilesRequest):
    profiles = []
    for p in req.profiles:
        d = p.model_dump()
        if not d.get("id"):
            d["id"] = str(uuid.uuid4())[:8]
        profiles.append(d)
    get_ai_service().save_profiles(profiles)
    return {"ok": True, "count": len(profiles)}


@router.post("/profiles/active")
async def set_active_profile(req: SetActiveProfileRequest):
    if req.profile:
        get_ai_service().set_active_profile(req.profile.model_dump())
    else:
        get_ai_service().set_active_profile(None)
    return {"ok": True, "config": get_ai_service().get_config_info()}


@router.get("/profiles/active")
async def get_active_profile():
    p = get_ai_service().get_active_profile()
    return {"profile": p}


# ── Prompts ────────────────────────────────────────────────────────

@router.get("/prompts")
async def list_prompts():
    return {"prompts": get_ai_service().get_all_prompts()}


@router.post("/prompts")
async def save_prompt(req: PromptModel):
    d = req.model_dump()
    if not d.get("id"):
        d["id"] = "custom_" + str(uuid.uuid4())[:8]
    d["builtin"] = False
    get_ai_service().save_custom_prompt(d)
    return {"ok": True, "prompt": d}


@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str):
    if get_ai_service().delete_custom_prompt(prompt_id):
        return {"ok": True}
    raise HTTPException(404, "Prompt not found or is built-in")


@router.post("/prompts/active")
async def set_active_prompt(req: SetActivePromptRequest):
    get_ai_service().set_active_prompt(req.prompt_id)
    return {"ok": True, "active_prompt_id": req.prompt_id}


# ── Filter rule info (for AI context) ─────────────────────────────

@router.get("/filter-syntax")
async def filter_syntax():
    """Return filter syntax documentation for reference."""
    return {
        "operators": [
            {"op": "-eq", "desc": "Equal to"},
            {"op": "-ne", "desc": "Not equal to"},
            {"op": "-gt", "desc": "Greater than"},
            {"op": "-lt", "desc": "Less than"},
            {"op": "-ge", "desc": "Greater than or equal to"},
            {"op": "-le", "desc": "Less than or equal to"},
            {"op": "-contain", "desc": "Contains substring"},
            {"op": "-notcontain", "desc": "Does not contain"},
            {"op": "-match", "desc": "Regex match"},
            {"op": "-startswith", "desc": "Starts with"},
            {"op": "-endswith", "desc": "Ends with"},
        ],
        "logic": ["&&", "||"],
        "example": 'ImageFileName -contain "svchost" && PID -gt 100',
        "current_columns": get_service().get_current_columns(engine_id="vol3"),
    }


# ── Conversations (persistent history) ────────────────────────────

class RenameConversationRequest(BaseModel):
    title: str


class CreateConversationRequest(BaseModel):
    title: str = ""
    engine: str = "vol3"


@router.get("/conversations")
async def list_conversations():
    """Return all saved conversations, newest first."""
    return {"conversations": conv_store.list_conversations()}


@router.post("/conversations")
async def create_conversation(req: CreateConversationRequest):
    """Explicitly create a new empty conversation."""
    meta = conv_store.create_conversation(title=req.title, engine=req.engine)
    return {"ok": True, "conversation": meta}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Return full message transcript for a conversation."""
    meta = conv_store.get_conversation(conv_id)
    if not meta:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    messages = conv_store.get_messages(conv_id)
    return {"conversation": meta, "messages": messages}


@router.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, req: RenameConversationRequest):
    """Rename a conversation."""
    meta = conv_store.rename_conversation(conv_id, req.title)
    if not meta:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"ok": True, "conversation": meta}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and its messages."""
    if not conv_store.delete_conversation(conv_id):
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"ok": True}


@router.post("/conversations/{conv_id}/load")
async def load_conversation(conv_id: str):
    """Load a saved conversation into the ai_service in-memory history
    so subsequent /chat calls continue from that point."""
    meta = conv_store.get_conversation(conv_id)
    if not meta:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    messages = conv_store.get_messages(conv_id)
    ai = get_ai_service()
    ai.clear_history(conv_id)
    for msg in messages:
        if msg.get("role") in ("user", "assistant", "tool"):
            clean_msg = {"role": msg["role"]}
            if "content" in msg:
                clean_msg["content"] = msg["content"]
            if msg.get("role") == "tool":
                clean_msg.update({
                    "tool_call_id": msg.get("tool_call_id"),
                    "toolName": msg.get("toolName"),
                    "toolArgs": msg.get("toolArgs"),
                    "toolSummary": msg.get("toolSummary"),
                    "toolError": msg.get("toolError"),
                })
            ai.append_history(conv_id, clean_msg)
    return {"ok": True, "loaded": len(messages), "conversation": meta}
