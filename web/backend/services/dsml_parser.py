"""DSML (in-band tool-call) stream parser.

Some OpenAI-compatible models — notably DeepSeek's ``v4-pro`` family — emit
tool calls as *text* inside the ``delta.content`` stream rather than as
structured ``delta.tool_calls`` deltas.  The in-band protocol uses a custom
markup that looks like::

    <｜｜DSML｜｜tool_calls>
    <｜｜DSML｜｜invoke name="run_plugin">
    <｜｜DSML｜｜parameter name="plugin_name" string="true">pslist.PsList</｜｜DSML｜｜parameter>
    <｜｜DSML｜｜parameter name="pid" string="false">4376</｜｜DSML｜｜parameter>
    </｜｜DSML｜｜invoke>
    </｜｜DSML｜｜tool_calls>

(Note the *fullwidth* pipe character ``｜`` U+FF5C, not the ASCII ``|``.)

Without this parser, that text is streamed verbatim to the user terminal and
the tools are never executed.  ``DSMLStreamParser`` consumes the text stream
chunk-by-chunk, strips the protocol markup, and yields ``(visible_text,
tool_calls)`` so the agent loop can execute the calls via the same path used
for standard ``delta.tool_calls``.

The parser is intentionally conservative:

* It only acts when it sees the ``OPEN`` marker.  Models that never emit DSML
  pay zero overhead — every chunk is returned as visible text immediately.
* A malformed / unclosed block is flushed as plain text on ``flush()`` so no
  user-visible content is ever silently dropped.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Fullwidth pipe used as the DSML delimiter (U+FF5C), NOT ASCII '|'.
_PIPE = "\uff5c"
_MARKER = f"{_PIPE}{_PIPE}DSML{_PIPE}{_PIPE}"  # ｜｜DSML｜｜
OPEN_TAG = f"<{_MARKER}tool_calls>"
CLOSE_TAG = f"</{_MARKER}tool_calls>"

# A single ``<｜｜DSML｜｜invoke name="X"> … </｜｜DSML｜｜invoke>`` block.
_INVOKE_RE = re.compile(
    rf"<{_MARKER}invoke\s+name=\"([^\"]+)\">(.*?)</{_MARKER}invoke>",
    re.DOTALL,
)

# Each ``<｜｜DSML｜｜parameter name="Y" [string="true"|"false"]>VALUE</…>``.
_PARAM_RE = re.compile(
    rf"<{_MARKER}parameter\s+name=\"([^\"]+)\""
    rf"(?:\s+string=\"(true|false)\")?\s*>(.*?)</{_MARKER}parameter>",
    re.DOTALL,
)

# A partial-open probe: the longest suffix of the buffer that is also a
# prefix of ``OPEN_TAG``.  When OPEN_TAG straddles two chunks (e.g. the first
# chunk ends with ``<｜｜DSML`` and the next starts with ``｜｜tool_calls>``),
# we must hold that suffix back rather than emit it as visible text.
def _trailing_open_prefix(buf: str) -> int:
    """Return the length of the longest suffix of ``buf`` that is a prefix of
    ``OPEN_TAG``.  0 means no prefix match — the whole buffer is safe to emit.
    """
    if not buf:
        return 0
    max_len = min(len(buf), len(OPEN_TAG))
    for length in range(max_len, 0, -1):
        if OPEN_TAG.startswith(buf[-length:]):
            return length
    return 0


class DSMLStreamParser:
    """Stateful parser fed by successive ``delta.content`` chunks."""

    def __init__(self) -> None:
        self._buffer: str = ""
        self._in_block: bool = False

    def feed(self, text: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Absorb a chunk of streamed content.

        Returns ``(visible_chunks, tool_calls)`` where ``visible_chunks`` is a
        list of text fragments confirmed to be user-visible (safe to stream to
        the frontend) and ``tool_calls`` is a list of fully-parsed tool calls
        in the same shape as ``AiService._accumulate_tool_calls``::

            {"id": "", "function_name": str, "function_arguments": <json str>}
        """
        if not text:
            return [], []
        self._buffer += text
        if self._in_block:
            return self._drain_block()
        return self._drain_plain()

    def flush(self) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Finalise the stream, returning any residual visible text.

        An unclosed DSML block is treated as malformed and emitted as plain
        text (OPEN_TAG re-prepended) rather than discarded — this guarantees
        we never silently drop user-visible content.
        """
        if not self._buffer:
            return [], []
        residual = self._buffer
        self._buffer = ""
        was_in_block = self._in_block
        self._in_block = False
        if was_in_block:
            # We were mid-block when the stream ended: the OPEN_TAG had
            # already been consumed from the buffer, so re-attach it so the
            # caller sees the full malformed fragment as visible text.
            residual = OPEN_TAG + residual
        if residual.strip():
            return [residual], []
        return [], []

    # ── internal ────────────────────────────────────────────────────

    def _drain_plain(self) -> Tuple[List[str], List[Dict[str, Any]]]:
        """We are NOT inside a DSML block.  Look for OPEN_TAG."""
        visible: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        while True:
            idx = self._buffer.find(OPEN_TAG)
            if idx == -1:
                # No complete open tag.  The tail of the buffer might be a
                # partial prefix of ``OPEN_TAG`` that straddles chunks — hold
                # it back; emit everything before it as visible text.
                prefix_len = _trailing_open_prefix(self._buffer)
                safe_end = len(self._buffer) - prefix_len
                if safe_end > 0:
                    visible.append(self._buffer[:safe_end])
                    self._buffer = self._buffer[safe_end:]
                # keep the partial prefix in the buffer for next feed
                break

            # Emit anything before the open tag as visible text.
            if idx > 0:
                visible.append(self._buffer[:idx])
            self._buffer = self._buffer[idx + len(OPEN_TAG):]
            self._in_block = True
            # Now try to drain the block we just entered.
            block_visible, block_calls = self._drain_block()
            visible.extend(block_visible)
            tool_calls.extend(block_calls)
            # If the block is still incomplete (_in_block remained True),
            # stop processing — we must wait for more chunks to arrive.
            if self._in_block:
                break

        # Drop any empty strings so callers don't emit no-op chunks.
        visible = [v for v in visible if v]
        return visible, tool_calls

    def _drain_block(self) -> Tuple[List[str], List[Dict[str, Any]]]:
        """We are inside a DSML block.  Look for CLOSE_TAG."""
        idx = self._buffer.find(CLOSE_TAG)
        if idx == -1:
            # Block still streaming; hold the whole buffer.
            return [], []

        block_text = self._buffer[:idx]
        self._buffer = self._buffer[idx + len(CLOSE_TAG):]
        self._in_block = False

        tool_calls = _parse_block(block_text)
        if not tool_calls:
            # Block parsed to nothing useful — surface the raw text so the
            # model's output isn't lost (better a visible oddity than silent
            # data loss).
            logger.warning("DSML block produced no tool calls: %r", block_text[:200])
            return [OPEN_TAG + block_text + CLOSE_TAG], []

        # After draining a complete block we may be back in plain mode with
        # more buffer left (e.g. trailing text, or another OPEN_TAG).  Recurse.
        if self._buffer:
            more_visible, more_calls = self._drain_plain()
            return more_visible, tool_calls + more_calls
        return [], tool_calls


def _parse_block(block_text: str) -> List[Dict[str, Any]]:
    """Parse a complete DSML block (between OPEN and CLOSE) into tool calls."""
    calls: List[Dict[str, Any]] = []
    for invoke_match in _INVOKE_RE.finditer(block_text):
        function_name = invoke_match.group(1).strip()
        body = invoke_match.group(2)
        arguments: Dict[str, Any] = {}
        for param_match in _PARAM_RE.finditer(body):
            key = param_match.group(1)
            is_string = param_match.group(2) == "true"
            raw_value = param_match.group(3)
            arguments[key] = _coerce_value(raw_value, is_string)
        calls.append({
            "id": "",
            "function_name": function_name,
            "function_arguments": json.dumps(arguments, ensure_ascii=False),
        })
    return calls


def _coerce_value(raw: str, is_string: bool) -> Any:
    """Coerce a DSML parameter value to its Python type.

    DSML carries an explicit ``string="true|false"`` hint.  When absent or
    "true", the value is kept as a string.  Otherwise we attempt JSON parsing
    so integer pids/offsets arrive as ints (matching the agent arg coercion).
    """
    raw = raw.strip()
    if is_string:
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Fall back to the raw string if it doesn't parse as JSON.
        return raw
