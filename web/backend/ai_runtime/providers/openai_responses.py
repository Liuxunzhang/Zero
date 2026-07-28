"""OpenAI Responses API adapter."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from ..models import ProviderEvent, Usage
from .base import (
    ProviderAdapter,
    ProviderRequest,
    classify_provider_exception,
    normalize_stop_reason,
    normalize_tool_id,
)
from .converters import openai_responses_input, responses_tools


class OpenAIResponsesAdapter(ProviderAdapter):
    protocol = "openai_responses"

    def __init__(self, *, api_key: str = "", base_url: str = "", client: Any = None) -> None:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key or "sk-placeholder", base_url=base_url or None)
        self.client = client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        snap = request.snapshot
        kwargs: dict[str, Any] = {
            "model": snap.model,
            "input": openai_responses_input(request.messages),
            "instructions": snap.system_prompt or None,
            "max_output_tokens": snap.max_output_tokens,
            "stream": True,
        }
        if snap.tools:
            kwargs["tools"] = responses_tools(snap.tools)
        if snap.model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
            if snap.reasoning_level == "off":
                kwargs["reasoning"] = {"effort": "none"}
            else:
                kwargs["reasoning"] = {"effort": snap.reasoning_level}
                if snap.thinking_summary:
                    kwargs["reasoning"]["summary"] = "auto"
        yield ProviderEvent("message_start", {"provider": self.protocol, "model": snap.model})
        calls: dict[str, dict[str, str]] = {}
        usage = Usage()
        stop_reason = ""
        request_id = ""
        try:
            stream = await self.client.responses.create(**kwargs)
            async for event in stream:
                kind = str(getattr(event, "type", ""))
                if kind in {"response.output_text.delta", "response.refusal.delta"}:
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        yield ProviderEvent("text_delta", {"text": delta})
                elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_summary.delta"}:
                    delta = getattr(event, "delta", "") or ""
                    if delta and snap.thinking_summary:
                        yield ProviderEvent("thinking_summary_delta", {"text": delta})
                elif kind == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", "") == "function_call":
                        key = str(getattr(item, "id", "") or getattr(item, "call_id", ""))
                        calls[key] = {
                            "id": str(getattr(item, "call_id", "") or key),
                            "name": str(getattr(item, "name", "") or ""),
                            "arguments": str(getattr(item, "arguments", "") or ""),
                        }
                elif kind == "response.function_call_arguments.delta":
                    key = str(getattr(event, "item_id", "") or getattr(event, "output_index", 0))
                    item = calls.setdefault(key, {"id": key, "name": "", "arguments": ""})
                    delta = str(getattr(event, "delta", "") or "")
                    item["arguments"] += delta
                    yield ProviderEvent("tool_call_delta", {
                        "tool_call_id": normalize_tool_id(item["id"]),
                        "name": item["name"],
                        "arguments_delta": delta,
                    })
                elif kind == "response.function_call_arguments.done":
                    key = str(getattr(event, "item_id", "") or getattr(event, "output_index", 0))
                    item = calls.setdefault(key, {"id": key, "name": "", "arguments": ""})
                    item["arguments"] = str(getattr(event, "arguments", "") or item["arguments"])
                elif kind == "response.output_item.done":
                    output = getattr(event, "item", None)
                    if getattr(output, "type", "") == "function_call":
                        key = str(
                            getattr(output, "id", "")
                            or getattr(output, "call_id", "")
                            or getattr(event, "output_index", 0)
                        )
                        calls[key] = {
                            "id": str(getattr(output, "call_id", "") or key),
                            "name": str(getattr(output, "name", "") or ""),
                            "arguments": str(getattr(output, "arguments", "") or ""),
                        }
                elif kind in {"response.completed", "response.incomplete", "response.failed"}:
                    response = getattr(event, "response", None)
                    request_id = str(getattr(response, "id", "") or request_id)
                    usage = usage.merge(Usage.from_mapping(getattr(response, "usage", None) or {}))
                    status = str(getattr(response, "status", "") or "")
                    details = getattr(response, "incomplete_details", None)
                    stop_reason = str(getattr(details, "reason", "") or status or stop_reason)
        except Exception as exc:
            raise classify_provider_exception(exc)

        truncated = stop_reason in {"max_output_tokens", "incomplete"}
        for item in calls.values():
            raw = item["arguments"]
            complete = not truncated
            try:
                arguments = json.loads(raw)
                complete = complete and isinstance(arguments, dict)
            except (TypeError, ValueError):
                arguments = {}
                complete = False
            yield ProviderEvent("tool_call", {
                "tool_call_id": normalize_tool_id(item["id"]),
                "name": item["name"],
                "arguments": arguments,
                "raw_arguments": raw,
                "complete": complete,
            })
        yield ProviderEvent("usage", usage.to_dict())
        yield ProviderEvent("message_end", {
            "stop_reason": normalize_stop_reason(stop_reason, has_tool_calls=bool(calls)),
            "request_id": request_id,
            "usage": usage.to_dict(),
        })

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            await close()
