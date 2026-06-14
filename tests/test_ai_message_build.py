"""Tests for ``AiService`` message-construction and tool-result compaction.

These exercise the pure helper methods (no LLM, no vol3, no network):
``_compact_tool_result_for_messages`` — token budget for tool results folded
back into the agent ``messages`` list.
"""

from __future__ import annotations

import json

import pytest

from web.backend.services.ai_service import AiService


def _make_result(rows: int = 100, columns: int = 5, **extra) -> dict:
    """Build a realistic ``_execute_agent_tool`` run_plugin result."""
    cols = [f"col_{i}" for i in range(columns)]
    data = [tuple(f"r{r}_c{c}" for c in range(columns)) for r in range(rows)]
    result = {
        "plugin": "windows.pslist.PsList",
        "columns": cols,
        "rows": data,
        "total": rows,
        "truncated": rows > 100,
        "summary": f"插件返回 {rows} 行",
    }
    result.update(extra)
    return result


# ── _compact_tool_result_for_messages ────────────────────────────────

def test_compact_truncates_rows_to_budget():
    result = _make_result(rows=100)
    compacted = json.loads(AiService._compact_tool_result_for_messages(result))
    assert len(compacted["rows"]) == AiService._TOOL_RESULT_HISTORY_MAX_ROWS
    # Metadata about the truncation is recorded so the model knows rows were dropped.
    assert compacted["history_rows_truncated"] == 100 - AiService._TOOL_RESULT_HISTORY_MAX_ROWS
    assert compacted["total"] == 100


def test_compact_preserves_summary_and_columns():
    result = _make_result(rows=10)
    compacted = json.loads(AiService._compact_tool_result_for_messages(result))
    assert compacted["summary"] == "插件返回 10 行"
    assert compacted["columns"][:5] == ["col_0", "col_1", "col_2", "col_3", "col_4"]
    assert compacted["plugin"] == "windows.pslist.PsList"


def test_compact_keeps_files_without_details():
    result = _make_result(rows=5, files=[
        {"path": "/dumps/a.dmp", "size_bytes": 1024},
        {"path": "/dumps/b.dmp", "size_bytes": 2048},
    ], details=[{"pid": 1}, {"pid": 2}])
    compacted = json.loads(AiService._compact_tool_result_for_messages(result))
    assert len(compacted["files"]) == 2
    assert compacted["files"][0] == {"path": "/dumps/a.dmp", "size_bytes": 1024}
    # ``details`` is collapsed to a count — the per-dump dict is NOT inlined.
    assert "details" not in compacted
    assert compacted["details_count"] == 2


def test_compact_drops_none_values():
    """Fields whose value resolves to None are omitted, keeping JSON tight."""
    result = {"plugin": "x", "rows": [], "columns": [], "total": 0}
    compacted = json.loads(AiService._compact_tool_result_for_messages(result))
    # Empty rows list is falsy but we keep it explicit; ensure no stray None keys.
    assert "truncated" not in compacted or compacted["truncated"] is not None
    assert "summary" not in compacted  # summary was None → dropped


def test_compact_is_much_smaller_than_full_result():
    """The compact digest should be substantially smaller than the full JSON."""
    result = _make_result(rows=100, columns=10)
    full = len(json.dumps(result, ensure_ascii=False))
    compact = len(AiService._compact_tool_result_for_messages(result))
    assert compact < full / 3  # at least 3x smaller


def test_compact_handles_non_dict_input():
    """Defensive: a non-dict result is serialised as-is without crashing."""
    out = AiService._compact_tool_result_for_messages("just a string")
    assert json.loads(out) == "just a string"


def test_compact_capped_column_count():
    """Very wide results still keep a bounded column list."""
    result = _make_result(rows=5, columns=50)
    compacted = json.loads(AiService._compact_tool_result_for_messages(result))
    assert len(compacted["columns"]) <= 30


# ── _summarize_tool_result (existing helper, pin its contract) ───────

def test_summarize_includes_summary_and_files():
    result = _make_result(rows=5, summary="pslist ran ok", files=[
        {"path": "/dumps/x", "size_bytes": 10},
    ])
    summary = AiService._summarize_tool_result(result)
    assert "pslist ran ok" in summary
    assert "/dumps/x" in summary
    assert "10 bytes" in summary


def test_summarize_marks_truncated():
    result = _make_result(rows=200, truncated=True)
    summary = AiService._summarize_tool_result(result)
    assert "截断" in summary


def test_summarize_empty_for_non_dict():
    assert AiService._summarize_tool_result("x") == ""
    assert AiService._summarize_tool_result(None) == ""


# ── memory-compression debounce ─────────────────────────────────────
#
# ``_schedule_memory_update`` is debounced: a real LLM compression only fires
# after N turns OR M seconds.  We assert the *firing decision* by counting
# how many times ``asyncio.create_task`` is invoked — without running an
# actual LLM call.  The async body itself is replaced with a no-op stub.

import asyncio
import time
from unittest.mock import patch

from web.backend.services import ai_service as ai_mod


def _make_service(ai_data_dir, monkeypatch):
    """Construct an AiService whose on-disk paths point at a tmp dir."""
    monkeypatch.setattr(ai_mod, "_AI_DATA_DIR", ai_data_dir)
    monkeypatch.setattr(ai_mod, "_PROFILES_FILE", ai_data_dir / "profiles.json")
    monkeypatch.setattr(ai_mod, "_PROMPTS_FILE", ai_data_dir / "prompts.json")
    monkeypatch.setattr(ai_mod, "_SETTINGS_FILE", ai_data_dir / "settings.json")
    monkeypatch.setattr(ai_mod, "_MEMORY_FILE", ai_data_dir / "compressed_memory.json")
    monkeypatch.setattr(ai_mod, "_MEMORY_ITEMS_FILE", ai_data_dir / "memory_items.json")
    monkeypatch.setattr(ai_mod, "_MEMORY_STATS_FILE", ai_data_dir / "memory_stats.json")
    return AiService()


def _patch_create_task_collector(monkeypatch):
    """Replace ``asyncio.create_task`` with a collector that closes the
    coroutine instead of scheduling it.  Returns the list of captured
    coroutines so the test can assert how many times firing happened."""
    created: list = []

    def _fake(coro):
        created.append(coro)
        # Close immediately so we don't leak "coroutine never awaited" warnings.
        try:
            coro.close()
        except Exception:
            pass
        return coro

    monkeypatch.setattr(asyncio, "create_task", _fake)
    return created


def test_memory_debounce_suppresses_until_turn_threshold(ai_data_dir, monkeypatch):
    """Three sub-threshold turns must NOT trigger a compression task."""
    svc = _make_service(ai_data_dir, monkeypatch)
    created = _patch_create_task_collector(monkeypatch)

    for _ in range(ai_mod._MEMORY_DEBOUNCE_TURNS - 1):
        svc._schedule_memory_update("u", "k", "m", "q", "a", None)

    assert created == []  # nothing fired yet
    assert svc._memory_turns_since_compress == ai_mod._MEMORY_DEBOUNCE_TURNS - 1
    assert svc._memory_status == "queued"


def test_memory_debounce_fires_on_turn_threshold(ai_data_dir, monkeypatch):
    svc = _make_service(ai_data_dir, monkeypatch)
    created = _patch_create_task_collector(monkeypatch)

    for _ in range(ai_mod._MEMORY_DEBOUNCE_TURNS):
        svc._schedule_memory_update("u", "k", "m", "q", "a", None)

    assert len(created) == 1  # the third turn crossed the threshold
    # Counters reset on fire.
    assert svc._memory_turns_since_compress == 0


def test_memory_debounce_fires_after_time_threshold(ai_data_dir, monkeypatch):
    """Even below the turn threshold, enough elapsed time must fire."""
    svc = _make_service(ai_data_dir, monkeypatch)
    # Pretend the last compression happened long ago.
    svc._memory_last_compress_ts = time.monotonic() - ai_mod._MEMORY_DEBOUNCE_SECONDS - 1

    created = _patch_create_task_collector(monkeypatch)

    svc._schedule_memory_update("u", "k", "m", "q", "a", None)
    assert len(created) == 1


def test_clear_all_memory_resets_debounce(ai_data_dir, monkeypatch):
    svc = _make_service(ai_data_dir, monkeypatch)
    # Bump the counters to simulate prior activity.
    svc._memory_turns_since_compress = 2
    svc._memory_last_compress_ts = time.monotonic()
    svc._memory_status = "queued"

    svc.clear_all_memory()

    assert svc._memory_turns_since_compress == 0
    assert svc._memory_last_compress_ts == 0.0
    assert svc._memory_status == "idle"


def test_memory_debounce_noop_when_disabled(ai_data_dir, monkeypatch):
    svc = _make_service(ai_data_dir, monkeypatch)
    svc._ai_memory_enabled = False
    created = _patch_create_task_collector(monkeypatch)
    # Even beyond thresholds, disabled memory must not fire.
    for _ in range(ai_mod._MEMORY_DEBOUNCE_TURNS + 2):
        svc._schedule_memory_update("u", "k", "m", "q", "a", None)
    assert created == []


# ── config.py persistence decoupling ────────────────────────────────
#
# Runtime profile / settings changes must NEVER rewrite the git-tracked
# ``config.py`` file.  They are persisted under ``.zero/ai/`` only, and
# optionally mirrored into the in-memory ``config`` module for legacy
# fallback readers.

from pathlib import Path

from zero import config as zero_config


def _config_py_snapshot() -> str:
    """Snapshot the current config.py contents (read-only)."""
    cfg_path = Path(zero_config.__file__)
    return cfg_path.read_text("utf-8")


def test_save_profiles_does_not_touch_config_py(ai_data_dir, monkeypatch):
    snapshot = _config_py_snapshot()
    svc = _make_service(ai_data_dir, monkeypatch)
    svc._persist_to_config_py = True  # even with persist enabled

    svc.save_profiles([
        {"id": "t1", "name": "test-model", "base_url": "https://x.test", "api_key": "secret", "model": "m1"},
    ])

    assert _config_py_snapshot() == snapshot
    # In-memory mirror is still kept in sync for legacy readers.
    assert any(p.get("id") == "t1" for p in zero_config.AI_PROFILES)


def test_save_ai_settings_does_not_touch_config_py(ai_data_dir, monkeypatch):
    snapshot = _config_py_snapshot()
    svc = _make_service(ai_data_dir, monkeypatch)
    svc._persist_to_config_py = True

    svc.save_ai_settings({
        "ai_max_tokens": 8192,
        "ai_temperature": 0.7,
        "ai_max_history": 20,
        "ai_context_max_rows": 1000,
    })

    assert _config_py_snapshot() == snapshot
    # In-memory mirror updated.
    assert zero_config.AI_MAX_HISTORY == 20


def test_set_active_profile_does_not_touch_config_py(ai_data_dir, monkeypatch):
    snapshot = _config_py_snapshot()
    svc = _make_service(ai_data_dir, monkeypatch)
    svc._persist_to_config_py = True

    svc.set_active_profile(
        {"id": "t1", "name": "test", "base_url": "https://x.test", "api_key": "k", "model": "m"}
    )

    assert _config_py_snapshot() == snapshot
    assert zero_config.AI_BASE_URL == "https://x.test"


def test_set_persist_to_config_does_not_touch_config_py(ai_data_dir, monkeypatch):
    """Enabling persist must not trigger a config.py rewrite."""
    snapshot = _config_py_snapshot()
    svc = _make_service(ai_data_dir, monkeypatch)

    svc.set_persist_to_config(True)

    assert _config_py_snapshot() == snapshot
