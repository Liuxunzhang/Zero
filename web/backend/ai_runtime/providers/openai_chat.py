"""OpenAI-compatible Chat Completions adapter, including textual DSML."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from web.backend.services.tool_markup import DsmlStreamFilter, parse_dsml_tool_calls

from ..models import ProviderEvent, Usage
from .base import (
    ProviderAdapter,
    ProviderRequest,
    classify_provider_exception,
    normalize_stop_reason,
    normalize_tool_id,
)
from .converters import openai_chat_messages, openai_tools


class OpenAIChatAdapter(ProviderAdapter):
    protocol = "openai_chat"

    def __init__(self, *, api_key: str = "", base_url: str = "", client: Any = None) -> None:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key or "sk-placeholder", base_url=base_url or None)
        self.client = client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        snapshot = request.snapshot
        kwargs: dict[str, Any] = {
            "model": snapshot.model,
            "messages": openai_chat_messages(request.messages, snapshot.system_prompt),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": snapshot.max_output_tokens,
        }
        if snapshot.tools:
            kwargs.update(tools=openai_tools(snapshot.tools), tool_choice="auto")
        if snapshot.model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["reasoning_effort"] = (
                "none" if snapshot.reasoning_level == "off" else snapshot.reasoning_level
            )
        yield ProviderEvent("message_start", {"provider": self.protocol, "model": snapshot.model})
        calls: dict[int, dict[str, str]] = {}
        raw_text: list[str] = []
        filter_ = DsmlStreamFilter()
        stop_reason = ""
        request_id = ""
        usage = Usage()
        try:
            stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in stream:
                request_id = request_id or str(getattr(chunk, "id", "") or "")
                usage_raw = getattr(chunk, "usage", None)
                if usage_raw:
                    usage = usage.merge(Usage.from_mapping(usage_raw))
                    yield ProviderEvent("usage", usage.to_dict())
                for choice in getattr(chunk, "choices", []) or []:
                    stop_reason = getattr(choice, "finish_reason", None) or stop_reason
                    delta = getattr(choice, "delta", None)
                    if not delta:
                        continue
                    content = getattr(delta, "content", None) or ""
                    if content:
                        raw_text.append(content)
                        visible = filter_.feed(content)
                        if visible:
                            yield ProviderEvent("text_delta", {"text": visible})
                    reasoning = (
                        getattr(delta, "reasoning_summary", None)
                        or ""
                    )
                    if reasoning and snapshot.thinking_summary:
                        yield ProviderEvent("thinking_summary_delta", {"text": reasoning})
                    for tc in getattr(delta, "tool_calls", None) or []:
                        index = int(getattr(tc, "index", 0) or 0)
                        item = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        item["id"] += getattr(tc, "id", None) or ""
                        fn = getattr(tc, "function", None)
                        if fn:
                            item["name"] += getattr(fn, "name", None) or ""
                            fragment = getattr(fn, "arguments", None) or ""
                            item["arguments"] += fragment
                            yield ProviderEvent("tool_call_delta", {
                                "index": index,
                                "tool_call_id": item["id"],
                                "name": item["name"],
                                "arguments_delta": fragment,
                            })
            tail = filter_.finish()
            if tail:
                yield ProviderEvent("text_delta", {"text": tail})
        except Exception as exc:
            raise classify_provider_exception(exc)

        if not calls:
            for index, call in enumerate(parse_dsml_tool_calls("".join(raw_text))):
                calls[index] = {
                    "id": f"dsml_{index}",
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                }
        truncated = stop_reason in {"length", "max_tokens"}
        for index in sorted(calls):
            call = calls[index]
            raw = call["arguments"]
            complete = not truncated
            try:
                arguments = json.loads(raw)
                complete = complete and isinstance(arguments, dict)
            except (TypeError, ValueError):
                arguments = {}
                complete = False
            yield ProviderEvent("tool_call", {
                "tool_call_id": normalize_tool_id(call["id"] or str(index)),
                "name": call["name"],
                "arguments": arguments,
                "raw_arguments": raw,
                "complete": complete,
            })
        yield ProviderEvent("message_end", {
            "stop_reason": normalize_stop_reason(stop_reason, has_tool_calls=bool(calls)),
            "request_id": request_id,
            "usage": usage.to_dict(),
        })

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            await close()
