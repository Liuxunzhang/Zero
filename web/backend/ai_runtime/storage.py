"""Append-only conversation, run-event and credential persistence."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Message, ToolResultMessage, message_from_dict, message_to_dict, now_iso


SCHEMA_VERSION = 2


def atomic_json(path: Path, value: Any, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
    if mode is not None:
        os.chmod(temporary, mode)
    os.replace(temporary, path)
    if mode is not None:
        os.chmod(path, mode)


def safe_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))
    if not clean:
        raise ValueError("invalid identifier")
    return clean


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.index_file = self.root / "index.json"
        self._index_lock = threading.RLock()
        self._locks: dict[str, threading.RLock] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self.migrate_legacy()

    def _lock(self, conversation_id: str) -> threading.RLock:
        with self._index_lock:
            return self._locks.setdefault(conversation_id, threading.RLock())

    def _path(self, conversation_id: str) -> Path:
        return self.root / f"{safe_id(conversation_id)}.jsonl"

    def _load_index(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.index_file.read_text("utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    def _save_index(self, items: list[dict[str, Any]]) -> None:
        atomic_json(self.index_file, items)

    def list(self) -> list[dict[str, Any]]:
        with self._index_lock:
            return sorted(self._load_index(), key=lambda item: item.get("updated_at", ""), reverse=True)

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self._index_lock:
            return next((dict(item) for item in self._load_index() if item.get("id") == conversation_id), None)

    def create(
        self,
        *,
        title: str = "新对话",
        engine_id: str = "vol3",
        image_id: str = "",
    ) -> dict[str, Any]:
        with self._index_lock:
            conversation_id = uuid.uuid4().hex[:12]
            timestamp = now_iso()
            item = {
                "version": SCHEMA_VERSION,
                "id": conversation_id,
                "title": title or "新对话",
                "engine_id": engine_id,
                "engine": engine_id,
                "image_id": image_id,
                "created_at": timestamp,
                "updated_at": timestamp,
                "message_count": 0,
                "active_run_id": None,
            }
            index = self._load_index()
            index.append(item)
            self._save_index(index)
            self._path(conversation_id).touch(exist_ok=True)
            return dict(item)

    def ensure_writable(self, conversation_id: str, engine_id: str, image_id: str) -> dict[str, Any]:
        item = self.get(conversation_id)
        if item is None:
            raise KeyError(conversation_id)
        if item.get("engine_id", item.get("engine", "vol3")) != engine_id:
            raise ValueError("conversation is pinned to a different engine")
        pinned = str(item.get("image_id") or "")
        if pinned and pinned != image_id:
            raise ValueError("conversation is pinned to a different image; create a new conversation")
        if not pinned and image_id:
            self.update_meta(conversation_id, image_id=image_id)
        return item

    def update_meta(self, conversation_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._index_lock:
            index = self._load_index()
            item = next((entry for entry in index if entry.get("id") == conversation_id), None)
            if item is None:
                return None
            item.update(changes)
            item["updated_at"] = now_iso()
            self._save_index(index)
            return dict(item)

    def append_entry(
        self,
        conversation_id: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        lock = self._lock(conversation_id)
        with lock:
            path = self._path(conversation_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            if parent_id is None and path.exists():
                try:
                    with path.open("rb") as existing:
                        existing.seek(0, os.SEEK_END)
                        position = existing.tell() - 2
                        while position >= 0:
                            existing.seek(position)
                            if existing.read(1) == b"\n":
                                break
                            position -= 1
                        existing.seek(max(0, position + 1))
                        last_line = existing.readline().decode("utf-8")
                    if last_line.strip():
                        parent_id = json.loads(last_line).get("entry_id")
                except (OSError, ValueError):
                    parent_id = None
            entry = {
                "version": SCHEMA_VERSION,
                "entry_id": uuid.uuid4().hex,
                "parent_id": parent_id,
                "timestamp": now_iso(),
                "type": entry_type,
                **payload,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return entry

    def append_message(
        self,
        conversation_id: str,
        message: Message,
        *,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        entry = self.append_entry(
            conversation_id,
            "message",
            {"message": message_to_dict(message)},
            parent_id=parent_id,
        )
        with self._index_lock:
            index = self._load_index()
            item = next((meta for meta in index if meta.get("id") == conversation_id), None)
            if item is not None:
                item["updated_at"] = entry["timestamp"]
                item["message_count"] = int(item.get("message_count", 0)) + 1
                if item.get("title") in {"", "新对话"} and message_to_dict(message).get("role") == "user":
                    text = str(message_to_dict(message).get("content", "")).strip().replace("\n", " ")
                    item["title"] = text[:40] + ("…" if len(text) > 40 else "")
                self._save_index(index)
        return entry

    def entries(self, conversation_id: str) -> list[dict[str, Any]]:
        path = self._path(conversation_id)
        if not path.exists():
            return []
        result: list[dict[str, Any]] = []
        with self._lock(conversation_id), path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        result.append(item)
                except ValueError:
                    continue
        return result

    def messages(self, conversation_id: str) -> list[Message]:
        result: list[Message] = []
        for entry in self.entries(conversation_id):
            if entry.get("type") == "history_reset":
                result.clear()
            elif entry.get("type") == "message" and isinstance(entry.get("message"), dict):
                result.append(message_from_dict(entry["message"]))
        return result

    def checkpoints(self, conversation_id: str) -> list[dict[str, Any]]:
        result = []
        for entry in self.entries(conversation_id):
            if entry.get("type") == "history_reset":
                result.clear()
            elif entry.get("type") == "checkpoint":
                result.append(entry)
        return result

    def delete(self, conversation_id: str) -> bool:
        with self._index_lock:
            index = self._load_index()
            filtered = [item for item in index if item.get("id") != conversation_id]
            if len(filtered) == len(index):
                return False
            self._save_index(filtered)
        self._path(conversation_id).unlink(missing_ok=True)
        legacy = self.root / f"{safe_id(conversation_id)}.json"
        legacy.unlink(missing_ok=True)
        legacy.with_name(legacy.name + ".bak-v1").unlink(missing_ok=True)
        return True

    def migrate_legacy(self) -> None:
        """Convert legacy ``{id}.json`` arrays without discarding originals."""
        with self._index_lock:
            index = self._load_index()
            changed = False
            for meta in index:
                conversation_id = str(meta.get("id") or "")
                if not conversation_id:
                    continue
                legacy = self.root / f"{safe_id(conversation_id)}.json"
                target = self._path(conversation_id)
                if not legacy.exists() or target.exists():
                    continue
                try:
                    raw = json.loads(legacy.read_text("utf-8"))
                    messages = raw if isinstance(raw, list) else raw.get("messages", [])
                except (OSError, ValueError, AttributeError):
                    continue
                backup = legacy.with_name(legacy.name + ".bak-v1")
                if not backup.exists():
                    shutil.copy2(legacy, backup)
                previous: str | None = None
                for item in messages:
                    if not isinstance(item, dict):
                        continue
                    # A legacy tool card represents an assistant tool call followed by its result.
                    if item.get("role") == "tool" and item.get("toolName"):
                        call_id = str(item.get("tool_call_id") or uuid.uuid4().hex)
                        assistant = message_from_dict({
                            "role": "assistant",
                            "content": [{
                                "type": "tool_call",
                                "tool_call_id": call_id,
                                "name": item.get("toolName"),
                                "arguments": item.get("toolArgs") or {},
                                "complete": True,
                            }],
                            "created_at": item.get("ts"),
                        })
                        entry = self.append_entry(conversation_id, "message", {"message": message_to_dict(assistant)}, parent_id=previous)
                        previous = entry["entry_id"]
                        result = ToolResultMessage(
                            tool_call_id=call_id,
                            content=str(item.get("toolSummary") or item.get("toolError") or ""),
                            details={"legacy_summary_only": True, "tool_name": item.get("toolName")},
                            is_error=bool(item.get("toolError")),
                            created_at=item.get("ts") or now_iso(),
                        )
                        entry = self.append_entry(conversation_id, "message", {"message": message_to_dict(result)}, parent_id=previous)
                    else:
                        message = message_from_dict(item)
                        entry = self.append_entry(conversation_id, "message", {"message": message_to_dict(message)}, parent_id=previous)
                    previous = entry["entry_id"]
                meta["version"] = SCHEMA_VERSION
                meta["engine_id"] = meta.get("engine_id") or meta.get("engine") or "vol3"
                meta.setdefault("image_id", "")
                meta["message_count"] = len(self.messages(conversation_id))
                changed = True
            if changed:
                self._save_index(index)


class EventStore:
    def __init__(self, root: Path, retention_days: int = 7) -> None:
        self.root = Path(root)
        self.retention_days = retention_days
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}

    def _path(self, run_id: str) -> Path:
        return self.root / f"{safe_id(run_id)}.jsonl"

    def append(self, run_id: str, event: dict[str, Any]) -> None:
        lock = self._locks.setdefault(run_id, threading.RLock())
        with lock, self._path(run_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()

    def read(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        path = self._path(run_id)
        if not path.exists():
            return []
        result = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                    if int(event.get("seq", 0)) > after_seq:
                        result.append(event)
                except (ValueError, TypeError):
                    continue
        return result

    def cleanup(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        removed = 0
        for path in self.root.glob("*.jsonl"):
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, str]:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            return {str(k): str(v) for k, v in raw.items() if v}
        except (OSError, ValueError):
            return {}

    def save(self, values: dict[str, str]) -> None:
        atomic_json(self.path, values, mode=0o600)

    def set(self, profile_id: str, api_key: str) -> None:
        values = self.load()
        if api_key:
            values[profile_id] = api_key
        else:
            values.pop(profile_id, None)
        self.save(values)

    def masked(self, profile_id: str) -> dict[str, Any]:
        value = self.load().get(profile_id, "")
        if not value:
            return {"has_api_key": False, "api_key_mask": ""}
        suffix = value[-4:] if len(value) >= 4 else "****"
        return {"has_api_key": True, "api_key_mask": f"••••••••{suffix}"}

    def migrate_profiles(self, profiles_path: Path) -> list[dict[str, Any]]:
        try:
            profiles = json.loads(Path(profiles_path).read_text("utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(profiles, list):
            return []
        keys = self.load()
        changed = False
        for profile in profiles:
            key = str(profile.pop("api_key", "") or "")
            profile_id = str(profile.get("id") or "")
            if key and profile_id:
                keys[profile_id] = key
                changed = True
        if changed:
            self.save(keys)
            atomic_json(Path(profiles_path), profiles)
        return profiles
