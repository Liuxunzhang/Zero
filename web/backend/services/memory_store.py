"""Structured memory storage and retrieval for AI context management."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_ts() -> int:
    return int(time.time())


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z0-9_]{2,}", str(text or "").lower())
    return set(tokens)


def _safe_float(value: Any, fallback: float = 0.5) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _entity_key(content: str, source_plugin: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(content or "").lower()).strip("_")
    return f"{source_plugin}:{normalized[:80] or 'unknown'}"


def build_memory_items_from_text(
    assistant_text: str,
    source_plugin: str,
    item_type: str = "fact",
    confidence: float = 0.6,
) -> List[Dict[str, Any]]:
    """Extract concise memory items from assistant output."""
    lines = [ln.strip() for ln in str(assistant_text or "").splitlines() if ln.strip()]
    candidates: List[str] = []
    for ln in lines:
        if ln.startswith(("- ", "* ", "• ", "1. ", "2. ", "3. ")):
            candidates.append(ln.lstrip("-*• ").strip())

    if not candidates:
        # Fallback: keep first 6 informative sentences/lines.
        for ln in lines:
            if len(ln) >= 12:
                candidates.append(ln)
            if len(candidates) >= 6:
                break

    items: List[Dict[str, Any]] = []
    now = _now_ts()
    for text in candidates[:12]:
        content = text[:600]
        items.append(
            {
                "id": uuid.uuid4().hex[:12],
                "type": item_type,
                "content": content,
                "source_plugin": source_plugin or "none",
                "created_at": now,
                "confidence": max(0.0, min(1.0, _safe_float(confidence, 0.6))),
                "entity_key": _entity_key(content, source_plugin or "none"),
                "superseded": False,
                "superseded_by": None,
            }
        )
    return items


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically using a temp file + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, "utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


class MemoryStore:
    """Persistent memory item store with retrieval and lifecycle utilities."""

    def __init__(self, items_file: Path, stats_file: Optional[Path] = None) -> None:
        self.items_file = Path(items_file)
        self.stats_file = Path(stats_file) if stats_file else self.items_file.with_name("memory_stats.json")
        self.items_file.parent.mkdir(parents=True, exist_ok=True)
        self._items: List[Dict[str, Any]] = self._load_items()
        self._stats: Dict[str, Any] = self._load_stats()

    def _load_items(self) -> List[Dict[str, Any]]:
        if not self.items_file.exists():
            return []
        try:
            raw = json.loads(self.items_file.read_text("utf-8"))
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
        except Exception as e:
            logger.warning("Failed to load memory items: %s", e)
        return []

    def _save_items(self) -> None:
        _atomic_write(self.items_file, json.dumps(self._items, ensure_ascii=False, indent=2))

    def _load_stats(self) -> Dict[str, Any]:
        defaults = {
            "retrieval_calls": 0,
            "retrieved_items_total": 0,
            "compression_runs": 0,
            "compression_failures": 0,
            "last_compression_ms": 0,
            "last_updated_at": 0,
        }
        if not self.stats_file.exists():
            return defaults
        try:
            raw = json.loads(self.stats_file.read_text("utf-8"))
            if isinstance(raw, dict):
                defaults.update(raw)
        except Exception as e:
            logger.warning("Failed to load memory stats: %s", e)
        return defaults

    def _save_stats(self) -> None:
        _atomic_write(self.stats_file, json.dumps(self._stats, ensure_ascii=False, indent=2))

    def _record_stats(self, **kwargs: Any) -> None:
        self._stats.update(kwargs)
        self._stats["last_updated_at"] = _now_ts()
        self._save_stats()

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "items_total": len(self._items),
            "items_active": sum(1 for i in self._items if not i.get("superseded")),
            "items_file": str(self.items_file),
        }

    def record_compression_result(self, ok: bool, elapsed_ms: int) -> None:
        self._stats["compression_runs"] = int(self._stats.get("compression_runs", 0)) + 1
        if not ok:
            self._stats["compression_failures"] = int(self._stats.get("compression_failures", 0)) + 1
        self._stats["last_compression_ms"] = int(elapsed_ms)
        self._record_stats()

    def upsert_items(self, new_items: List[Dict[str, Any]]) -> int:
        """Insert items and supersede conflicting active entries by entity_key."""
        if not new_items:
            return 0
        inserted = 0
        by_key = {
            str(item.get("entity_key")): item
            for item in self._items
            if not item.get("superseded") and item.get("entity_key")
        }
        for item in new_items:
            if not isinstance(item, dict) or not item.get("content"):
                continue
            entry = dict(item)
            entry.setdefault("id", uuid.uuid4().hex[:12])
            entry.setdefault("type", "fact")
            entry.setdefault("source_plugin", "none")
            entry.setdefault("created_at", _now_ts())
            entry.setdefault("confidence", 0.5)
            entry.setdefault(
                "entity_key",
                _entity_key(str(entry.get("content", "")), str(entry.get("source_plugin", "none"))),
            )
            entry["superseded"] = bool(entry.get("superseded", False))
            entry["superseded_by"] = entry.get("superseded_by")

            existing = by_key.get(entry["entity_key"])
            if existing and not existing.get("superseded"):
                existing["superseded"] = True
                existing["superseded_by"] = entry["id"]

            self._items.append(entry)
            by_key[entry["entity_key"]] = entry
            inserted += 1
        if inserted:
            self._save_items()
        return inserted

    def cleanup(self, ttl_days: int = 30, max_items: int = 2000) -> int:
        """Remove stale and overflow memory entries."""
        ttl_days = max(1, int(ttl_days))
        max_items = max(200, int(max_items))
        cutoff = _now_ts() - ttl_days * 24 * 3600

        before = len(self._items)
        self._items = [
            item
            for item in self._items
            if int(item.get("created_at", 0)) >= cutoff
        ]
        self._items.sort(key=lambda i: int(i.get("created_at", 0)), reverse=True)
        if len(self._items) > max_items:
            self._items = self._items[:max_items]
        removed = before - len(self._items)
        if removed > 0:
            self._save_items()
        return removed

    def retrieve(
        self,
        query: str,
        source_plugin: str = "",
        top_k: int = 8,
        max_chars: int = 4000,
    ) -> List[Dict[str, Any]]:
        """Retrieve top memory items by lexical overlap + source + recency."""
        top_k = max(1, int(top_k))
        max_chars = max(500, int(max_chars))
        q_tokens = _tokenize(query)
        now = _now_ts()

        scored: List[tuple[float, Dict[str, Any]]] = []
        for item in self._items:
            if item.get("superseded"):
                continue
            content = str(item.get("content", ""))
            if not content:
                continue
            tokens = _tokenize(content)
            overlap = len(q_tokens & tokens) if q_tokens else 0
            plugin_boost = 1.0 if source_plugin and item.get("source_plugin") == source_plugin else 0.0
            conf = _safe_float(item.get("confidence"), 0.5)
            age_hours = max(1.0, (now - int(item.get("created_at", now))) / 3600.0)
            recency = 1.0 / age_hours
            score = overlap * 3.0 + plugin_boost + conf * 0.5 + recency
            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected: List[Dict[str, Any]] = []
        used_chars = 0
        for _, item in scored[: max(top_k * 4, top_k)]:
            payload = (
                f"[{item.get('source_plugin','none')}] "
                f"{item.get('type','fact')} "
                f"(conf={_safe_float(item.get('confidence'), 0.5):.2f}) "
                f"{item.get('content','')}"
            )
            if used_chars + len(payload) > max_chars:
                break
            selected.append(item)
            used_chars += len(payload)
            if len(selected) >= top_k:
                break

        self._stats["retrieval_calls"] = int(self._stats.get("retrieval_calls", 0)) + 1
        self._stats["retrieved_items_total"] = int(self._stats.get("retrieved_items_total", 0)) + len(selected)
        self._record_stats()
        return selected
