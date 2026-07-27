"""Compatibility helpers for providers that emit DSML tool calls as text.

Some OpenAI-compatible model gateways return function calls inside
``<｜｜DSML｜｜tool_calls>`` tags in ``delta.content`` instead of populating
``delta.tool_calls``.  These helpers recover those calls and keep the transport
markup out of the user-visible response.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

_DSML_PREFIX = r"[|｜]{2}DSML[|｜]{2}"
_DSML_START_RE = re.compile(
    rf"<{_DSML_PREFIX}(?P<kind>tool_calls|invoke)\b[^>]*>",
    re.IGNORECASE,
)
_DSML_PREFIX_RE = re.compile(rf"<{_DSML_PREFIX}", re.IGNORECASE)
_DSML_TOOL_BLOCK_RE = re.compile(
    rf"<{_DSML_PREFIX}tool_calls\b[^>]*>.*?</{_DSML_PREFIX}tool_calls\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    rf"<{_DSML_PREFIX}invoke\b(?P<attrs>[^>]*)>"
    rf"(?P<body>.*?)"
    rf"</{_DSML_PREFIX}invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_PARAMETER_RE = re.compile(
    rf"<{_DSML_PREFIX}parameter\b(?P<attrs>[^>]*)>"
    rf"(?P<value>.*?)"
    rf"</{_DSML_PREFIX}parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_TAG_RE = re.compile(rf"</?{_DSML_PREFIX}[^>]*>", re.IGNORECASE)
_ATTRIBUTE_RE = re.compile(
    r"""(?P<name>[\w:-]+)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.DOTALL,
)
_CLOSE_PATTERNS = {
    "tool_calls": re.compile(
        rf"</{_DSML_PREFIX}tool_calls\s*>",
        re.IGNORECASE,
    ),
    "invoke": re.compile(
        rf"</{_DSML_PREFIX}invoke\s*>",
        re.IGNORECASE,
    ),
}


def _attributes(raw: str) -> dict[str, str]:
    return {
        match.group("name").lower(): html.unescape(match.group("value"))
        for match in _ATTRIBUTE_RE.finditer(raw or "")
    }


def _parameter_value(raw: str, string_hint: str) -> Any:
    value = html.unescape(raw.strip())
    if string_hint.strip().lower() == "true":
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def parse_dsml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract DSML invocations into ``name`` / ``arguments`` dictionaries."""
    calls: list[dict[str, Any]] = []
    for invoke_match in _DSML_INVOKE_RE.finditer(text or ""):
        invoke_attrs = _attributes(invoke_match.group("attrs"))
        tool_name = invoke_attrs.get("name", "").strip()
        if not tool_name:
            continue

        arguments: dict[str, Any] = {}
        for parameter_match in _DSML_PARAMETER_RE.finditer(invoke_match.group("body")):
            parameter_attrs = _attributes(parameter_match.group("attrs"))
            parameter_name = parameter_attrs.get("name", "").strip()
            if not parameter_name:
                continue
            arguments[parameter_name] = _parameter_value(
                parameter_match.group("value"),
                parameter_attrs.get("string", ""),
            )

        calls.append({"name": tool_name, "arguments": arguments})
    return calls


def strip_dsml_tool_markup(text: str) -> str:
    """Remove complete or dangling DSML transport markup from visible text."""
    cleaned = _DSML_TOOL_BLOCK_RE.sub("", text or "")
    cleaned = _DSML_INVOKE_RE.sub("", cleaned)

    # A truncated provider response may end in the middle of a DSML block.  In
    # that case, preserve the preceding explanation and discard the protocol
    # fragment rather than exposing it in the chat.
    dangling = _DSML_PREFIX_RE.search(cleaned)
    if dangling:
        cleaned = cleaned[:dangling.start()]

    return _DSML_TAG_RE.sub("", cleaned)


class DsmlStreamFilter:
    """Incrementally hide DSML blocks while preserving normal streamed text."""

    _HOLD_BACK = 64

    def __init__(self) -> None:
        self._pending = ""
        self._active_kind = ""

    def feed(self, content: str) -> str:
        self._pending += content or ""
        visible_parts: list[str] = []

        while self._pending:
            if self._active_kind:
                closing = _CLOSE_PATTERNS[self._active_kind].search(self._pending)
                if not closing:
                    break
                self._pending = self._pending[closing.end():]
                self._active_kind = ""
                continue

            opening = _DSML_START_RE.search(self._pending)
            if opening:
                visible_parts.append(self._pending[:opening.start()])
                self._pending = self._pending[opening.end():]
                self._active_kind = opening.group("kind").lower()
                continue

            if len(self._pending) <= self._HOLD_BACK:
                break
            visible_parts.append(self._pending[:-self._HOLD_BACK])
            self._pending = self._pending[-self._HOLD_BACK:]

        return "".join(visible_parts)

    def finish(self) -> str:
        if self._active_kind:
            self._pending = ""
            self._active_kind = ""
            return ""
        visible = strip_dsml_tool_markup(self._pending)
        self._pending = ""
        return visible
