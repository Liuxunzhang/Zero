"""Tests for ``web.backend.services.conversation_store``.

The store resolves its on-disk directory at import time, so each test relies
on the ``conv_dir`` fixture to repoint it at a tmp path via monkeypatch.
"""

from __future__ import annotations

import json

import pytest

from web.backend.services import conversation_store as cs


def _make_user(content: str) -> dict:
    return {"role": "user", "content": content}


def _make_assistant(content: str) -> dict:
    return {"role": "assistant", "content": content}


def _make_tool() -> dict:
    return {
        "role": "tool",
        "tool_call_id": "call_1",
        "toolName": "run_plugin",
        "toolArgs": {"plugin_name": "pslist.PsList"},
        "toolRunning": False,
        "toolSummary": "ran pslist",
        "toolError": "",
    }


# ── create / list ────────────────────────────────────────────────────

def test_create_conversation_returns_metadata(conv_dir):
    meta = cs.create_conversation(engine="vol3")
    assert meta["id"]
    assert meta["engine"] == "vol3"
    assert meta["message_count"] == 0
    assert (conv_dir / f"{meta['id']}.json").exists()


def test_list_conversations_newest_first(conv_dir):
    """``updated_at`` has only second precision, so to make ordering
    deterministic we craft distinct timestamps directly in the index file."""
    a = cs.create_conversation()
    b = cs.create_conversation()
    # Force distinct updated_at values: a older, b newer.
    index = cs._load_index()
    for meta in index:
        if meta["id"] == a["id"]:
            meta["updated_at"] = "2026-01-01T00:00:00Z"
        if meta["id"] == b["id"]:
            meta["updated_at"] = "2026-06-01T00:00:00Z"
    cs._save_index(index)

    listed = cs.list_conversations()
    ids = [c["id"] for c in listed]
    # b (newer) should appear before a (older).
    assert ids.index(b["id"]) < ids.index(a["id"])


# ── append / get ─────────────────────────────────────────────────────

def test_append_and_get_messages(conv_dir):
    meta = cs.create_conversation()
    cs.append_messages(meta["id"], [_make_user("hi"), _make_assistant("hello")])
    msgs = cs.get_messages(meta["id"])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    # All messages get a timestamp injected.
    assert all("ts" in m for m in msgs)


def test_append_preserves_tool_message_fields(conv_dir):
    """Tool messages carry camelCase fields the frontend renders verbatim."""
    meta = cs.create_conversation()
    tool = _make_tool()
    cs.append_messages(meta["id"], [tool])
    msgs = cs.get_messages(meta["id"])
    m = msgs[0]
    assert m["role"] == "tool"
    assert m["toolName"] == "run_plugin"
    assert m["toolArgs"] == {"plugin_name": "pslist.PsList"}
    assert m["toolSummary"] == "ran pslist"


def test_append_auto_titles_from_first_user(conv_dir):
    meta = cs.create_conversation()  # default title "新对话"
    long_msg = "请帮我分析这个内存镜像里的可疑进程" * 5
    cs.append_messages(meta["id"], [_make_user(long_msg)])
    updated = cs.get_conversation(meta["id"])
    assert updated["title"]
    assert updated["title"] != "新对话"
    assert updated["title"].endswith("…") or len(updated["title"]) <= 40
    assert updated["message_count"] == 1


def test_append_does_not_override_custom_title(conv_dir):
    meta = cs.create_conversation(title="my custom title")
    cs.append_messages(meta["id"], [_make_user("hello")])
    assert cs.get_conversation(meta["id"])["title"] == "my custom title"


def test_append_to_unknown_conversation_returns_none(conv_dir):
    assert cs.append_messages("nonexistent-id", [_make_user("x")]) is None


# ── replace / rename / delete ────────────────────────────────────────

def test_replace_messages_wipes_history(conv_dir):
    meta = cs.create_conversation()
    cs.append_messages(meta["id"], [_make_user("old"), _make_assistant("old resp")])
    cs.replace_messages(meta["id"], [_make_user("fresh")])
    msgs = cs.get_messages(meta["id"])
    assert len(msgs) == 1
    assert msgs[0]["content"] == "fresh"


def test_rename_conversation(conv_dir):
    meta = cs.create_conversation()
    updated = cs.rename_conversation(meta["id"], "renamed title")
    assert updated["title"] == "renamed title"


def test_rename_unknown_returns_none(conv_dir):
    assert cs.rename_conversation("nope", "x") is None


def test_delete_conversation_removes_files(conv_dir):
    meta = cs.create_conversation()
    conv_file = conv_dir / f"{meta['id']}.json"
    assert conv_file.exists()
    assert cs.delete_conversation(meta["id"]) is True
    assert not conv_file.exists()
    assert cs.get_conversation(meta["id"]) is None
    # second delete is idempotent (returns False)
    assert cs.delete_conversation(meta["id"]) is False


def test_invalid_conversation_id_rejected(conv_dir):
    """An id that sanitises to empty (only punctuation) is rejected."""
    with pytest.raises(ValueError):
        cs.get_messages("!!!")


def test_conversation_id_sanitized_keeps_alphanumerics(conv_dir):
    """Punctuation is stripped but alphanumerics survive, so the lookup
    falls through to an empty file rather than raising.  This documents the
    current (lossy) sanitisation in ``_conv_file``."""
    # ``a/../b`` sanitises to ``ab`` → looks up a non-existent file, returns [].
    assert cs.get_messages("a/../b") == []


# ── message_count accounting ─────────────────────────────────────────

def test_message_count_reflects_appends(conv_dir):
    meta = cs.create_conversation()
    assert meta["message_count"] == 0
    cs.append_messages(meta["id"], [_make_user("1")])
    assert cs.get_conversation(meta["id"])["message_count"] == 1
    cs.append_messages(meta["id"], [_make_assistant("2"), _make_assistant("3")])
    assert cs.get_conversation(meta["id"])["message_count"] == 3
