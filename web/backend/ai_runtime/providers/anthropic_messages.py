"""Anthropic Messages adapter with text, public summary and tool streaming."""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

from ..models import ProviderEvent, Usage
from .base import (
    ProviderAdapter,
    ProviderRequest,
    classify_provider_exception,
    normalize_stop_reason,
    normalize_tool_id,
)
from .converters import anthropic_messages


class AnthropicMessagesAdapter(ProviderAdapter):
    protocol = "anthropic_messages"

    def __init__(self, *, api_key: str = "", base_url: str = "", client: Any = None) -> None:
        if client is None:
            from anthropic import AsyncAnthropic

            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = AsyncAnthropic(**kwargs)
        self.client = client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        snap = request.snapshot
        tools = [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema", {"type": "object"}),
                "strict": True,
            }
            for tool in snap.tools
        ]
        kwargs: dict[str, Any] = {
            "model": snap.model,
            "max_tokens": snap.max_output_tokens,
            "messages": anthropic_messages(request.messages),
            "stream": True,
        }
        if snap.system_prompt:
            kwargs["system"] = snap.system_prompt
        if tools:
            kwargs["tools"] = tools
        adaptive_model = re.search(
            r"(opus-(?:4-[678]|5)|sonnet-(?:4-6|5)|fable-5|mythos)",
            snap.model,
            re.IGNORECASE,
        )
        if snap.reasoning_level == "off" and re.search(r"opus-5", snap.model, re.IGNORECASE):
            kwargs["thinking"] = {"type": "disabled"}
        elif snap.reasoning_level != "off":
            # The adapter only emits summaries; opaque signatures/raw thinking
            # are deliberately not stored in canonical messages.
            if adaptive_model:
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": snap.reasoning_level}
            else:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": {
                    "low": 1024, "medium": 4096, "high": 8192
                }.get(snap.reasoning_level, 4096)}
        yield ProviderEvent("message_start", {"provider": self.protocol, "model": snap.model})
        blocks: dict[int, dict[str, Any]] = {}
        usage = Usage()
        stop_reason = ""
        request_id = ""
        try:
            stream = await self.client.messages.create(**kwargs)
            async for event in stream:
                kind = str(getattr(event, "type", ""))
                if kind == "message_start":
                    msg = getattr(event, "message", None)
                    request_id = str(getattr(msg, "id", "") or request_id)
                    usage = usage.merge(Usage.from_mapping(getattr(msg, "usage", None) or {}))
                elif kind == "content_block_start":
                    index = int(getattr(event, "index", 0) or 0)
                    block = getattr(event, "content_block", None)
                    block_type = str(getattr(block, "type", ""))
                    blocks[index] = {
                        "type": block_type,
                        "id": str(getattr(block, "id", "") or index),
                        "name": str(getattr(block, "name", "") or ""),
                        "arguments": "",
                        "input": getattr(block, "input", None),
                    }
                elif kind == "content_block_delta":
                    index = int(getattr(event, "index", 0) or 0)
                    delta = getattr(event, "delta", None)
                    delta_type = str(getattr(delta, "type", ""))
                    if delta_type == "text_delta":
                        text = str(getattr(delta, "text", "") or "")
                        if text:
                            yield ProviderEvent("text_delta", {"text": text})
                    elif delta_type in {"thinking_delta", "summary_delta"}:
                        # Only summary fields are public. Raw thinking deltas are
                        # ignored unless the SDK explicitly labels them summary.
                        text = str(getattr(delta, "summary", "") or "")
                        if delta_type == "summary_delta":
                            text = text or str(getattr(delta, "text", "") or "")
                        if text and snap.thinking_summary:
                            yield ProviderEvent("thinking_summary_delta", {"text": text})
                    elif delta_type == "input_json_delta":
                        item = blocks.setdefault(index, {
                            "type": "tool_use", "id": str(index), "name": "", "arguments": "", "input": None
                        })
                        fragment = str(getattr(delta, "partial_json", "") or "")
                        item["arguments"] += fragment
                        yield ProviderEvent("tool_call_delta", {
                            "tool_call_id": normalize_tool_id(item["id"], "toolu"),
                            "name": item["name"],
                            "arguments_delta": fragment,
                        })
                elif kind == "message_delta":
                    delta = getattr(event, "delta", None)
                    stop_reason = str(getattr(delta, "stop_reason", "") or stop_reason)
                    usage = usage.merge(Usage.from_mapping(getattr(event, "usage", None) or {}))
                    yield ProviderEvent("usage", usage.to_dict())
        except Exception as exc:
            raise classify_provider_exception(exc)

        truncated = stop_reason == "max_tokens"
        has_tool_calls = False
        for block in blocks.values():
            if block["type"] != "tool_use":
                continue
            has_tool_calls = True
            raw = block["arguments"]
            arguments = block["input"] if isinstance(block["input"], dict) and not raw else None
            complete = not truncated
            if arguments is None:
                try:
                    arguments = json.loads(raw or "{}")
                    complete = complete and isinstance(arguments, dict)
                except (TypeError, ValueError):
                    arguments, complete = {}, False
            yield ProviderEvent("tool_call", {
                "tool_call_id": normalize_tool_id(block["id"], "toolu"),
                "name": block["name"],
                "arguments": arguments,
                "raw_arguments": raw or json.dumps(arguments, ensure_ascii=False),
                "complete": complete,
            })
        yield ProviderEvent("message_end", {
            "stop_reason": normalize_stop_reason(
                stop_reason, has_tool_calls=has_tool_calls
            ),
            "request_id": request_id,
            "usage": usage.to_dict(),
        })

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            await close()
