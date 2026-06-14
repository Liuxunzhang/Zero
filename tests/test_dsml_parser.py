"""Tests for ``web.backend.services.dsml_parser``.

The parser strips DeepSeek-style in-band DSML tool-call markup from the
streamed ``delta.content`` and returns parsed tool calls.  All tests are pure
— no LLM, no network.
"""

from __future__ import annotations

import json

import pytest

from web.backend.services.dsml_parser import (
    CLOSE_TAG,
    OPEN_TAG,
    DSMLStreamParser,
)

# Fullwidth pipe constant for building sample payloads.
P = "\uff5c"
MARKER = f"{P}{P}DSML{P}{P}"


def _dsml(invokes: list[tuple[str, dict]]) -> str:
    """Build a complete DSML tool_calls block with the given invokes."""
    parts = [OPEN_TAG]
    for name, args in invokes:
        parts.append(f"<{MARKER}invoke name=\"{name}\">")
        for key, (value, is_string) in args.items():
            attr = f' string="true"' if is_string else f' string="false"'
            parts.append(
                f"<{MARKER}parameter name=\"{key}\"{attr}>{value}</{MARKER}parameter>"
            )
        parts.append(f"</{MARKER}invoke>")
    parts.append(CLOSE_TAG)
    return "".join(parts)


# ── no DSML passes through ───────────────────────────────────────────

def test_plain_text_passes_through():
    p = DSMLStreamParser()
    visible, calls = p.feed("Hello world 普通文本")
    assert visible == ["Hello world 普通文本"]
    assert calls == []


def test_plain_text_char_by_char():
    p = DSMLStreamParser()
    text = "Hello world 这是普通文本 no dsml here"
    collected = []
    for ch in text:
        v, _ = p.feed(ch)
        collected.extend(v)
    v, _ = p.flush()
    collected.extend(v)
    assert "".join(collected) == text


def test_empty_feed_is_noop():
    p = DSMLStreamParser()
    assert p.feed("") == ([], [])


def test_flush_empty_is_noop():
    p = DSMLStreamParser()
    assert p.flush() == ([], [])


# ── single invoke ────────────────────────────────────────────────────

def test_single_invoke_single_chunk():
    payload = _dsml([("run_plugin", {"plugin_name": ("pslist.PsList", True)})])
    p = DSMLStreamParser()
    visible, calls = p.feed(payload)
    assert visible == []
    assert len(calls) == 1
    assert calls[0]["function_name"] == "run_plugin"
    assert json.loads(calls[0]["function_arguments"]) == {"plugin_name": "pslist.PsList"}


def test_integer_pid_coerced():
    """string=\"false\" → value parsed as JSON (int for digits)."""
    payload = _dsml([("run_plugin", {
        "plugin_name": ("handles.Handles", True),
        "pid": ("4376", False),
    })])
    p = DSMLStreamParser()
    _, calls = p.feed(payload)
    args = json.loads(calls[0]["function_arguments"])
    assert args["pid"] == 4376
    assert isinstance(args["pid"], int)
    assert args["plugin_name"] == "handles.Handles"


def test_string_true_keeps_string_type():
    payload = _dsml([("run_plugin", {"pid": ("4376", True)})])
    p = DSMLStreamParser()
    _, calls = p.feed(payload)
    assert json.loads(calls[0]["function_arguments"])["pid"] == "4376"


def test_no_string_attr_defaults_to_string():
    """When string= attribute is absent, value stays a string."""
    raw = (
        OPEN_TAG
        + f'<{MARKER}invoke name="run_plugin">'
        + f'<{MARKER}parameter name="plugin_name">info.Info</{MARKER}parameter>'
        + f'</{MARKER}invoke>'
        + CLOSE_TAG
    )
    p = DSMLStreamParser()
    _, calls = p.feed(raw)
    assert json.loads(calls[0]["function_arguments"]) == {"plugin_name": "info.Info"}


# ── multiple invokes ─────────────────────────────────────────────────

def test_multiple_invokes_one_block():
    payload = _dsml([
        ("run_plugin", {"plugin_name": ("info.Info", True)}),
        ("run_plugin", {"plugin_name": ("unloadedmodules.UnloadedModules", True)}),
        ("run_plugin", {"plugin_name": ("getsids.GetSIDs", True)}),
    ])
    p = DSMLStreamParser()
    _, calls = p.feed(payload)
    assert [c["function_name"] for c in calls] == ["run_plugin", "run_plugin", "run_plugin"]
    names = [json.loads(c["function_arguments"])["plugin_name"] for c in calls]
    assert names == ["info.Info", "unloadedmodules.UnloadedModules", "getsids.GetSIDs"]


# ── cross-chunk streaming ────────────────────────────────────────────

def test_dsml_split_across_chunks():
    payload = _dsml([("run_plugin", {"plugin_name": ("pslist.PsList", True)})])
    p = DSMLStreamParser()
    mid = len(payload) // 2
    v1, c1 = p.feed(payload[:mid])
    v2, c2 = p.feed(payload[mid:])
    v3, c3 = p.flush()
    assert v1 == [] and v3 == []
    assert c1 == []  # nothing complete after first half
    assert len(c2) == 1  # complete after second half
    assert json.loads(c2[0]["function_arguments"]) == {"plugin_name": "pslist.PsList"}


def test_dsml_char_by_char():
    """Worst-case fragmentation: one character per feed()."""
    payload = _dsml([("run_plugin", {
        "plugin_name": ("handles.Handles", True),
        "pid": ("100", False),
    })])
    p = DSMLStreamParser()
    visible: list = []
    calls: list = []
    for ch in payload:
        v, c = p.feed(ch)
        visible.extend(v)
        calls.extend(c)
    v, c = p.flush()
    visible.extend(v)
    calls.extend(c)
    assert visible == []
    assert len(calls) == 1
    assert json.loads(calls[0]["function_arguments"]) == {"plugin_name": "handles.Handles", "pid": 100}


def test_open_tag_straddles_chunk_boundary():
    """The OPEN_TAG itself is split across two feeds."""
    p = DSMLStreamParser()
    cut = len(OPEN_TAG) - 3
    v1, c1 = p.feed(OPEN_TAG[:cut])
    v2, c2 = p.feed(OPEN_TAG[cut:] + _dsml([("run_plugin", {"plugin_name": ("x.Y", True)})]).replace(OPEN_TAG, "").replace(CLOSE_TAG, "") + CLOSE_TAG)
    v3, c3 = p.flush()
    assert v1 == []  # held back as partial prefix
    assert v3 == []
    assert len(c2) == 1


# ── mixed visible text + DSML ────────────────────────────────────────

def test_text_before_dsml_is_visible():
    p = DSMLStreamParser()
    payload = "正在分析..." + _dsml([("list_plugins", {})])
    visible, calls = p.feed(payload)
    assert visible == ["正在分析..."]
    assert len(calls) == 1


def test_text_after_dsml_is_visible():
    p = DSMLStreamParser()
    payload = _dsml([("list_plugins", {})]) + "...继续分析"
    visible, calls = p.feed(payload)
    assert "".join(visible) == "...继续分析"
    assert len(calls) == 1


def test_text_between_two_blocks():
    p = DSMLStreamParser()
    payload = (
        _dsml([("run_plugin", {"plugin_name": ("a.A", True)})])
        + "中间文本"
        + _dsml([("run_plugin", {"plugin_name": ("b.B", True)})])
    )
    visible, calls = p.feed(payload)
    assert "".join(visible) == "中间文本"
    assert len(calls) == 2


# ── malformed / unclosed ─────────────────────────────────────────────

def test_unclosed_block_flushed_as_text():
    """An unclosed DSML block must NOT be silently dropped on flush."""
    p = DSMLStreamParser()
    p.feed(OPEN_TAG + "garbage without close")
    visible, calls = p.flush()
    assert calls == []
    # The held-back content surfaces as visible text (lossless).
    assert OPEN_TAG in "".join(visible)


def test_block_with_no_invokes_surfaces_as_text():
    """A well-formed block that yields no tool calls is surfaced raw."""
    p = DSMLStreamParser()
    payload = OPEN_TAG + "no invokes here" + CLOSE_TAG
    visible, calls = p.feed(payload)
    assert calls == []
    assert payload in "".join(visible)


def test_malformed_invoke_does_not_crash():
    p = DSMLStreamParser()
    payload = OPEN_TAG + f'<{MARKER}invoke name="run_plugin">broken' + CLOSE_TAG
    # Should not raise; the block surfaces as text because no invokes parsed.
    visible, calls = p.feed(payload)
    assert calls == []


def test_partial_prefix_in_plain_text_is_not_lost():
    """A ``<`` followed by non-DSML text must eventually be emitted."""
    p = DSMLStreamParser()
    # First feed ends with '<' which could be an OPEN_TAG prefix.
    v1, _ = p.feed("text <")
    assert v1 == ["text "] or v1 == ["text"]  # the '<' is held back
    # Next feed is NOT a pipe → no longer an OPEN_TAG prefix.
    v2, _ = p.feed("not dsml")
    v3, _ = p.flush()
    combined = "".join(v1 + v2 + v3)
    assert combined == "text <not dsml"


# ── tool call shape ──────────────────────────────────────────────────

def test_tool_call_shape_matches_accumulate_tool_calls():
    """Parsed calls must match the dict shape that chat_stream expects."""
    payload = _dsml([("run_plugin", {"plugin_name": ("x.Y", True)})])
    p = DSMLStreamParser()
    _, calls = p.feed(payload)
    assert set(calls[0].keys()) == {"id", "function_name", "function_arguments"}
    assert calls[0]["id"] == ""
    assert isinstance(calls[0]["function_arguments"], str)  # JSON string


def test_function_arguments_is_valid_json():
    payload = _dsml([("run_plugin", {"plugin_name": ("x.Y", True), "pid": ("7", False)})])
    p = DSMLStreamParser()
    _, calls = p.feed(payload)
    # Must round-trip through json.loads.
    parsed = json.loads(calls[0]["function_arguments"])
    assert parsed == {"plugin_name": "x.Y", "pid": 7}
