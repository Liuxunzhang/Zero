"""Provider adapter protocol and cross-provider history normalization."""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..models import Message, ProviderEvent, TurnSnapshot


class ProviderError(RuntimeError):
    pass


class ProviderTransientError(ProviderError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ContextOverflowError(ProviderError):
    pass


@dataclass(slots=True)
class ProviderRequest:
    snapshot: TurnSnapshot
    messages: list[Message]
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_tool_id(value: str, prefix: str = "call") -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    if not clean:
        clean = "generated"
    if clean.startswith(f"{prefix}_"):
        return clean[:64]
    return f"{prefix}_{clean}"[:64]


def classify_provider_exception(exc: Exception) -> Exception:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    retry_after = None
    response = getattr(exc, "response", None)
    if response is not None:
        raw = getattr(response, "headers", {}).get("retry-after")
        try:
            retry_after = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            pass
    if status in {408, 409, 429, 500, 502, 503, 504}:
        return ProviderTransientError(str(exc), retry_after)
    if "context" in text and any(word in text for word in ("length", "window", "token", "maximum")):
        return ContextOverflowError(str(exc))
    return exc


def normalize_stop_reason(value: str, *, has_tool_calls: bool = False) -> str:
    reason = str(value or "").strip().lower()
    if has_tool_calls and reason not in {
        "length", "max_tokens", "max_output_tokens", "incomplete",
        "content_filter", "safety", "failed", "error",
    }:
        return "tool_use"
    if reason in {"", "stop", "completed", "end_turn", "stop_sequence", "finished"}:
        return "stop"
    if reason in {
        "length", "max_tokens", "max_output_tokens", "incomplete",
        "model_length", "token_limit",
    }:
        return "length"
    if reason in {"content_filter", "safety", "recitation", "blocked", "prohibited_content"}:
        return "content_filter"
    if reason in {"failed", "error"}:
        return "error"
    if reason in {"cancelled", "canceled", "aborted"}:
        return "cancelled"
    if reason in {"refusal", "pause_turn"}:
        return reason
    return reason


class ProviderAdapter(abc.ABC):
    protocol: str

    @abc.abstractmethod
    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Stream one immutable turn as normalized events."""
        if False:  # pragma: no cover - makes this an async generator for type checkers
            yield ProviderEvent("message_start")

    async def close(self) -> None:
        return None
