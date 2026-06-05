"""Persistent conversation store for AI chat history.

Conversations are stored under .zero/ai/conversations/:
  index.json          - list of conversation metadata
  {id}.json           - full message transcript for a single conversation

A message entry:
  {"role": "user"|"assistant", "content": str, "ts": ISO-8601 timestamp}
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONV_DIR = _PROJECT_ROOT / ".zero" / "ai" / "conversations"
_INDEX_FILE = _CONV_DIR / "index.json"

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_dir() -> None:
    _CONV_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conv_file(conv_id: str) -> Path:
    import re
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', conv_id)
    if not safe_id:
        raise ValueError("Invalid conversation ID")
    return _CONV_DIR / f"{safe_id}.json"


def _load_index() -> list[dict]:
    try:
        if _INDEX_FILE.exists():
            return json.loads(_INDEX_FILE.read_text("utf-8"))
    except Exception as exc:
        logger.warning("Failed to load conversation index: %s", exc)
    return []


def _save_index(index: list[dict]) -> None:
    _ensure_dir()
    _INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")


def _load_messages(conv_id: str) -> list[dict]:
    path = _conv_file(conv_id)
    try:
        if path.exists():
            return json.loads(path.read_text("utf-8"))
    except Exception as exc:
        logger.warning("Failed to load messages for %s: %s", conv_id, exc)
    return []


def _save_messages(conv_id: str, messages: list[dict]) -> None:
    _ensure_dir()
    _conv_file(conv_id).write_text(
        json.dumps(messages, ensure_ascii=False, indent=2), "utf-8"
    )


def _title_from_message(content: str, max_len: int = 40) -> str:
    """Derive a short title from the first user message."""
    text = content.strip().replace("\n", " ")
    return text[:max_len] + ("…" if len(text) > max_len else "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_conversations() -> list[dict]:
    """Return conversation metadata sorted newest-first."""
    with _lock:
        return sorted(_load_index(), key=lambda c: c.get("updated_at", ""), reverse=True)


def create_conversation(title: str = "", engine: str = "vol3") -> dict:
    """Create a new conversation and return its metadata."""
    with _lock:
        conv_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        meta = {
            "id": conv_id,
            "title": title or "新对话",
            "engine": engine,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        index = _load_index()
        index.append(meta)
        _save_index(index)
        _save_messages(conv_id, [])
        logger.info("Created conversation %s", conv_id)
        return meta


def get_conversation(conv_id: str) -> Optional[dict]:
    """Return metadata for a single conversation, or None if not found."""
    with _lock:
        for meta in _load_index():
            if meta["id"] == conv_id:
                return meta
    return None


def get_messages(conv_id: str) -> list[dict]:
    """Return all messages for a conversation."""
    with _lock:
        return _load_messages(conv_id)


def append_messages(
    conv_id: str,
    new_messages: list[dict],
    *,
    auto_title: bool = True,
) -> Optional[dict]:
    """Append new messages to a conversation and update metadata.

    Returns the updated metadata, or None if the conversation does not exist.
    """
    with _lock:
        index = _load_index()
        meta = next((m for m in index if m["id"] == conv_id), None)
        if meta is None:
            return None

        now = _now_iso()
        ts_msgs = [
            {**msg, "ts": msg.get("ts") or now}
            for msg in new_messages
        ]

        existing = _load_messages(conv_id)
        merged = existing + ts_msgs
        _save_messages(conv_id, merged)

        meta["updated_at"] = now
        meta["message_count"] = len(merged)

        # Auto-title from first user message if still default.
        if auto_title and meta.get("title") in ("", "新对话"):
            first_user = next(
                (m["content"] for m in merged if m.get("role") == "user"), ""
            )
            if first_user:
                meta["title"] = _title_from_message(first_user)

        _save_index(index)
        return dict(meta)


def rename_conversation(conv_id: str, new_title: str) -> Optional[dict]:
    """Rename a conversation. Returns updated metadata or None."""
    with _lock:
        index = _load_index()
        meta = next((m for m in index if m["id"] == conv_id), None)
        if meta is None:
            return None
        meta["title"] = new_title.strip() or meta["title"]
        meta["updated_at"] = _now_iso()
        _save_index(index)
        return dict(meta)


def delete_conversation(conv_id: str) -> bool:
    """Delete a conversation. Returns True on success."""
    with _lock:
        index = _load_index()
        new_index = [m for m in index if m["id"] != conv_id]
        if len(new_index) == len(index):
            return False
        _save_index(new_index)
        path = _conv_file(conv_id)
        if path.exists():
            path.unlink()
        logger.info("Deleted conversation %s", conv_id)
        return True


def replace_messages(conv_id: str, messages: list[dict]) -> Optional[dict]:
    """Replace all messages in a conversation (e.g. after loading into ai_service)."""
    with _lock:
        index = _load_index()
        meta = next((m for m in index if m["id"] == conv_id), None)
        if meta is None:
            return None
        now = _now_iso()
        ts_msgs = [{**m, "ts": m.get("ts") or now} for m in messages]
        _save_messages(conv_id, ts_msgs)
        meta["updated_at"] = now
        meta["message_count"] = len(ts_msgs)
        _save_index(index)
        return dict(meta)
