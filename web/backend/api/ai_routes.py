"""AI analysis SSE streaming routes + profile / prompt management."""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from web.backend.services.ai_service import get_ai_service
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


# ── SSE streaming ──────────────────────────────────────────────────

async def _sse_stream(
    user_message: str,
    plugin_context: Optional[dict],
    conversation_id: str,
):
    ai = get_ai_service()
    assistant_parts: list[str] = []
    try:
        async for event in ai.chat_stream(user_message, plugin_context):
            payload = json.dumps(event, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            if event.get("type") == "chunk":
                assistant_parts.append(event.get("content", ""))
        # Persist both turns to the conversation store.
        new_messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "".join(assistant_parts)},
        ]
        conv_store.append_messages(conversation_id, new_messages)
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
    if req.include_context:
        svc = get_service()
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
    return StreamingResponse(
        _sse_stream(message, plugin_context, conv_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── History ────────────────────────────────────────────────────────

@router.get("/history")
async def ai_history():
    return {"history": get_ai_service().get_history()}


@router.delete("/history")
async def clear_ai_history():
    get_ai_service().clear_history()
    return {"ok": True}


@router.delete("/memory")
async def clear_ai_memory():
    get_ai_service().clear_compressed_memory()
    return {"ok": True}


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
    ai.clear_history()
    for msg in messages:
        if msg.get("role") in ("user", "assistant"):
            ai._history.append({"role": msg["role"], "content": msg["content"]})
    return {"ok": True, "loaded": len(messages), "conversation": meta}
