"""Compatibility facade over the append-only Runtime conversation store."""

from __future__ import annotations

from typing import Any, Optional

from web.backend.ai_runtime.models import (
    AssistantMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)


def _store():
    # Lazy import avoids constructing providers/runtime during module import.
    from web.backend.ai_runtime.service import get_runtime

    return get_runtime().conversations


def list_conversations() -> list[dict[str, Any]]:
    return _store().list()


def create_conversation(title: str = "", engine: str = "vol3") -> dict[str, Any]:
    from web.backend.ai_runtime.service import get_runtime

    return get_runtime().create_conversation(title=title, engine_id=engine)


def get_conversation(conv_id: str) -> Optional[dict[str, Any]]:
    return _store().get(conv_id)


def get_messages(conv_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in _store().messages(conv_id):
        if isinstance(message, UserMessage):
            result.append({"role": "user", "content": message.content, "ts": message.created_at})
        elif isinstance(message, AssistantMessage):
            text = "".join(
                block.text for block in message.content if isinstance(block, TextBlock)
            )
            if text:
                result.append({
                    "role": "assistant",
                    "content": text,
                    "ts": message.created_at,
                    "status": message.status,
                })
            for block in message.content:
                if isinstance(block, ToolCallBlock):
                    result.append({
                        "role": "tool",
                        "tool_call_id": block.tool_call_id,
                        "toolName": block.name,
                        "toolArgs": block.arguments,
                        "toolRunning": False,
                        "toolSummary": "",
                        "toolError": "",
                        "ts": message.created_at,
                    })
        elif isinstance(message, ToolResultMessage):
            card = next(
                (
                    item for item in reversed(result)
                    if item.get("role") == "tool"
                    and item.get("tool_call_id") == message.tool_call_id
                ),
                None,
            )
            if card is None:
                card = {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "toolName": message.details.get("tool_name", ""),
                    "toolArgs": {},
                    "toolRunning": False,
                    "toolSummary": "",
                    "toolError": "",
                    "ts": message.created_at,
                }
                result.append(card)
            card["toolError" if message.is_error else "toolSummary"] = message.content
            card["details"] = message.details
    return result


def _append_legacy(conv_id: str, raw: dict[str, Any]) -> None:
    role = raw.get("role")
    timestamp = raw.get("ts")
    if role == "user":
        message = UserMessage(content=str(raw.get("content", "")))
    elif role == "assistant":
        message = AssistantMessage(
            content=[TextBlock(text=str(raw.get("content", "")))],
            status=raw.get("status", "complete"),
        )
    elif role in {"tool", "tool_result"}:
        call_id = str(raw.get("tool_call_id") or "")
        if raw.get("toolName"):
            _store().append_message(conv_id, AssistantMessage(content=[ToolCallBlock(
                tool_call_id=call_id,
                name=str(raw.get("toolName") or ""),
                arguments=dict(raw.get("toolArgs") or {}),
            )]))
        message = ToolResultMessage(
            tool_call_id=call_id,
            content=str(raw.get("content") or raw.get("toolSummary") or raw.get("toolError") or ""),
            details=dict(raw.get("details") or {}),
            is_error=bool(raw.get("is_error") or raw.get("toolError")),
        )
    else:
        return
    if timestamp:
        message.created_at = str(timestamp)
    _store().append_message(conv_id, message)


def append_messages(
    conv_id: str,
    new_messages: list[dict[str, Any]],
    *,
    auto_title: bool = True,
) -> Optional[dict[str, Any]]:
    if _store().get(conv_id) is None:
        return None
    for message in new_messages:
        _append_legacy(conv_id, message)
    return _store().get(conv_id)


def rename_conversation(conv_id: str, new_title: str) -> Optional[dict[str, Any]]:
    return _store().update_meta(conv_id, title=new_title.strip() or "新对话")


def delete_conversation(conv_id: str) -> bool:
    return _store().delete(conv_id)


def replace_messages(conv_id: str, messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if _store().get(conv_id) is None:
        return None
    _store().append_entry(conv_id, "history_reset", {"reason": "legacy_replace"})
    _store().update_meta(conv_id, message_count=0)
    for message in messages:
        _append_legacy(conv_id, message)
    return _store().get(conv_id)
