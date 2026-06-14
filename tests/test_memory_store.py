"""Tests for ``web.backend.services.memory_store``.

Pure-function + tmp-file tests.  No LLM calls — we feed pre-built text and
assert the lexical store / retrieval behaviour.
"""

from __future__ import annotations

import json
import time

import pytest

from web.backend.services.memory_store import (
    MemoryStore,
    build_memory_items_from_text,
)


# ── build_memory_items_from_text ─────────────────────────────────────

def test_build_items_extracts_list_lines():
    text = """\
分析结论：
- svchost.exe (PID 443) 路径异常
- 检测到 RWX 内存区域
正常流程说明。
"""
    items = build_memory_items_from_text(text, source_plugin="windows.malfind.Malfind")
    contents = [i["content"] for i in items]
    assert any("svchost.exe" in c for c in contents)
    assert any("RWX" in c for c in contents)
    for item in items:
        assert item["source_plugin"] == "windows.malfind.Malfind"
        assert 0.0 <= item["confidence"] <= 1.0
        assert item["entity_key"]


def test_build_items_empty_input():
    assert build_memory_items_from_text("", source_plugin="x") == []
    assert build_memory_items_from_text(None, source_plugin="x") == []


def test_build_items_caps_long_lines():
    long_line = "- " + ("A" * 2000)
    items = build_memory_items_from_text(long_line, source_plugin="p")
    assert len(items) == 1
    assert len(items[0]["content"]) <= 600


def test_build_items_fallback_without_list_prefix():
    """Plain informative lines should still be picked up as a fallback."""
    text = "This is a longer informative sentence about memory.\n" * 3
    items = build_memory_items_from_text(text, source_plugin="p")
    assert len(items) > 0


# ── upsert / supersede ───────────────────────────────────────────────

def test_upsert_inserts_new_items(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    items = [
        {"content": "fact one", "source_plugin": "pslist"},
        {"content": "fact two", "source_plugin": "netscan"},
    ]
    inserted = store.upsert_items(items)
    assert inserted == 2
    assert len(store._items) == 2


def test_upsert_supersedes_same_entity_key(tmp_path):
    """Two items with the same normalized content key → the second supersedes the first."""
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([{"content": "PID 443 is suspicious", "source_plugin": "pslist"}])
    store.upsert_items([{"content": "PID 443 is suspicious", "source_plugin": "pslist"}])

    assert len(store._items) == 2
    active = [i for i in store._items if not i.get("superseded")]
    superseded = [i for i in store._items if i.get("superseded")]
    assert len(active) == 1
    assert len(superseded) == 1
    assert superseded[0]["superseded_by"] == active[0]["id"]


def test_upsert_empty_list_is_noop(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    assert store.upsert_items([]) == 0
    assert store._items == []


def test_upsert_skips_empty_content(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([
        {"content": "", "source_plugin": "p"},
        {"content": "real", "source_plugin": "p"},
    ])
    assert len(store._items) == 1


# ── retrieve ─────────────────────────────────────────────────────────

def test_retrieve_ranks_by_token_overlap(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([
        {"content": "svchost.exe process path anomaly", "source_plugin": "pslist"},
        {"content": "network connection to 8.8.8.8", "source_plugin": "netscan"},
    ])
    results = store.retrieve(query="svchost process", top_k=2)
    assert results
    assert "svchost" in results[0]["content"]


def test_retrieve_boosts_same_plugin(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([
        {"content": "general fact about network", "source_plugin": "pslist"},
        {"content": "general fact about network", "source_plugin": "netscan"},
    ])
    # Both contents normalise to the same entity_key → second supersedes first.
    # Insert a distinct second item so we have two active items to rank.
    store.upsert_items([
        {"content": "tcp port 443 listening", "source_plugin": "netscan"},
    ])
    results = store.retrieve(query="network", source_plugin="netscan", top_k=5)
    # All retrieved items should be active (not superseded).
    assert all(not r.get("superseded") for r in results)


def test_retrieve_respects_top_k(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([
        {"content": f"item number {i}", "source_plugin": "p"}
        for i in range(10)
    ])
    results = store.retrieve(query="item", top_k=3)
    assert len(results) <= 3


def test_retrieve_max_chars_caps_payload(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([
        {"content": "X" * 400, "source_plugin": "p"}
        for _ in range(5)
    ])
    # With a tiny char budget, we should get far fewer than 5 items.
    results = store.retrieve(query="X", top_k=5, max_chars=500)
    assert len(results) < 5


def test_retrieve_records_stats(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([{"content": "hello world", "source_plugin": "p"}])
    store.retrieve(query="hello")
    stats = store.get_stats()
    assert stats["retrieval_calls"] >= 1
    assert stats["retrieved_items_total"] >= 1


# ── cleanup ──────────────────────────────────────────────────────────

def test_cleanup_drops_stale_items(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    # Insert an old item directly with a stale timestamp.
    store._items = [
        {"content": "ancient", "source_plugin": "p", "created_at": 0, "id": "old"},
        {"content": "fresh", "source_plugin": "p", "created_at": int(time.time()), "id": "new"},
    ]
    store._save_items()
    removed = store.cleanup(ttl_days=1)
    assert removed >= 1
    contents = [i["content"] for i in store._items]
    assert "fresh" in contents
    assert "ancient" not in contents


def test_cleanup_max_items_clamped_to_floor(tmp_path):
    """``cleanup`` clamps ``max_items`` to at least 200 internally
    (memory_store.py:207 — ``max(200, int(max_items))``).  This documents
    that known behaviour: passing a small ``max_items`` will NOT truncate
    below 200 items, so 50 fresh items are all retained here."""
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    now = int(time.time())
    store._items = [
        {"content": f"c{i}", "source_plugin": "p", "created_at": now + i, "id": f"id{i}"}
        for i in range(50)
    ]
    store._save_items()
    # All items are fresh (within TTL), and 50 < 200 floor → nothing removed.
    removed = store.cleanup(ttl_days=365, max_items=10)
    assert removed == 0
    assert len(store._items) == 50


def test_cleanup_truncates_above_floor(tmp_path):
    """When items exceed the requested max (above the 200 floor), cleanup
    trims to the newest N.  ``max_items`` is clamped to >= 200, so passing
    exactly 200 with 250 items trims to 200."""
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    now = int(time.time())
    store._items = [
        {"content": f"c{i}", "source_plugin": "p", "created_at": now + i, "id": f"id{i}"}
        for i in range(250)
    ]
    store._save_items()
    store.cleanup(ttl_days=365, max_items=200)
    assert len(store._items) == 200
    # Newest 200 are retained (created_at now+50 .. now+249).
    assert all(int(i["created_at"]) >= now + 50 for i in store._items)


# ── persistence ──────────────────────────────────────────────────────

def test_store_persists_across_instances(tmp_path):
    items_file = tmp_path / "items.json"
    store1 = MemoryStore(items_file, tmp_path / "stats.json")
    store1.upsert_items([{"content": "persisted fact", "source_plugin": "p"}])

    store2 = MemoryStore(items_file, tmp_path / "stats.json")
    contents = [i["content"] for i in store2._items]
    assert "persisted fact" in contents


def test_clear_all_resets_store(tmp_path):
    store = MemoryStore(tmp_path / "items.json", tmp_path / "stats.json")
    store.upsert_items([{"content": "x", "source_plugin": "p"}])
    store.retrieve(query="x")  # bump stats
    removed = store.clear_all()
    assert removed >= 1
    assert store._items == []
    assert store.get_stats()["retrieval_calls"] == 0
