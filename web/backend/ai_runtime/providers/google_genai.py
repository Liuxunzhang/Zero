"""Stateless Google GenerateContent adapter."""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from ..models import ProviderEvent, Usage
from .base import (
    ProviderAdapter,
    ProviderRequest,
    classify_provider_exception,
    normalize_stop_reason,
    normalize_tool_id,
)
from .converters import google_contents


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class GoogleGenAIAdapter(ProviderAdapter):
    protocol = "google_genai"

    def __init__(self, *, api_key: str = "", base_url: str = "", client: Any = None) -> None:
        if client is None:
            from google import genai

            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                from google.genai import types

                kwargs["http_options"] = types.HttpOptions(base_url=base_url)
            client = genai.Client(**kwargs)
        self.client = client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        snap = request.snapshot
        declarations = [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters_json_schema": tool.get("input_schema", {"type": "object"}),
            }
            for tool in snap.tools
        ]
        config: dict[str, Any] = {
            "system_instruction": snap.system_prompt or None,
            "max_output_tokens": snap.max_output_tokens,
            # Runtime, not the SDK, owns the tool loop.
            "automatic_function_calling": {"disable": True},
        }
        if declarations:
            config["tools"] = [{"function_declarations": declarations}]
        if snap.reasoning_level == "off":
            config["thinking_config"] = {
                "include_thoughts": False,
                "thinking_budget": 0,
            }
        else:
            config["thinking_config"] = {
                "include_thoughts": snap.thinking_summary,
                "thinking_budget": {"low": 1024, "medium": 4096, "high": 8192}.get(
                    snap.reasoning_level, 4096
                ),
            }
        yield ProviderEvent("message_start", {"provider": self.protocol, "model": snap.model})
        usage = Usage()
        stop_reason = ""
        request_id = ""
        calls: list[dict[str, Any]] = []
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=snap.model,
                contents=google_contents(request.messages),
                config=config,
            )
            async for chunk in stream:
                request_id = request_id or str(_value(chunk, "response_id", "") or "")
                metadata = _value(chunk, "usage_metadata")
                if metadata:
                    usage = usage.merge(Usage(
                        input_tokens=int(_value(metadata, "prompt_token_count", 0) or 0),
                        output_tokens=int(_value(metadata, "candidates_token_count", 0) or 0),
                        cache_read_tokens=int(_value(metadata, "cached_content_token_count", 0) or 0),
                        reasoning_tokens=int(_value(metadata, "thoughts_token_count", 0) or 0),
                    ))
                    yield ProviderEvent("usage", usage.to_dict())
                for candidate in _value(chunk, "candidates", []) or []:
                    stop_reason = str(_value(candidate, "finish_reason", "") or stop_reason)
                    content = _value(candidate, "content", {}) or {}
                    for part in _value(content, "parts", []) or []:
                        text = str(_value(part, "text", "") or "")
                        if text:
                            if bool(_value(part, "thought", False)):
                                yield ProviderEvent("thinking_summary_delta", {"text": text})
                            else:
                                yield ProviderEvent("text_delta", {"text": text})
                        call = _value(part, "function_call")
                        if call:
                            call_id = str(_value(call, "id", "") or uuid.uuid4().hex[:12])
                            args = _value(call, "args", {}) or {}
                            if not isinstance(args, dict):
                                try:
                                    args = dict(args)
                                except (TypeError, ValueError):
                                    args = {}
                            calls.append({
                                "tool_call_id": normalize_tool_id(call_id),
                                "name": str(_value(call, "name", "") or ""),
                                "arguments": args,
                            })
        except Exception as exc:
            raise classify_provider_exception(exc)

        truncated = stop_reason.lower() in {"max_tokens", "max_output_tokens"}
        for call in calls:
            yield ProviderEvent("tool_call", {
                **call,
                "raw_arguments": json.dumps(call["arguments"], ensure_ascii=False),
                "complete": not truncated,
            })
        yield ProviderEvent("message_end", {
            "stop_reason": normalize_stop_reason(stop_reason, has_tool_calls=bool(calls)),
            "request_id": request_id,
            "usage": usage.to_dict(),
        })

    async def close(self) -> None:
        close = getattr(getattr(self.client, "aio", None), "aclose", None)
        if close:
            await close()
