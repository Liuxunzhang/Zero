"""Lossless visible-message conversion for provider wire formats."""

from __future__ import annotations

import json
from typing import Any

from ..models import (
    Message,
    TextBlock,
    ThinkingSummaryBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)
from .base import normalize_tool_id


def _user_content(message: UserMessage) -> str:
    if not message.context:
        return message.content
    return f"{message.content}\n\n<current_forensic_context>\n{message.context}\n</current_forensic_context>"


def openai_chat_messages(
    messages: list[Message],
    system: str = "",
    provider_state: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})
    for message in messages:
        if isinstance(message, UserMessage):
            result.append({"role": "user", "content": _user_content(message)})
        elif isinstance(message, ToolResultMessage):
            result.append({
                "role": "tool",
                "tool_call_id": normalize_tool_id(message.tool_call_id),
                "content": message.content,
            })
        else:
            text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            summaries = [b.text for b in message.content if isinstance(b, ThinkingSummaryBlock)]
            if summaries:
                text = (text + "\n\n[思考摘要]\n" + "\n".join(summaries)).strip()
            calls = [b for b in message.content if isinstance(b, ToolCallBlock) and b.complete]
            item: dict[str, Any] = {"role": "assistant", "content": text or None}
            state = (provider_state or {}).get(message.message_id) or {}
            reasoning_content = state.get("reasoning_content")
            if reasoning_content:
                item["reasoning_content"] = str(reasoning_content)
            if calls:
                item["tool_calls"] = [
                    {
                        "id": normalize_tool_id(call.tool_call_id),
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.raw_arguments
                            or json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in calls
                ]
            result.append(item)
    return result


def openai_responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, UserMessage):
            items.append({"role": "user", "content": _user_content(message)})
        elif isinstance(message, ToolResultMessage):
            items.append({
                "type": "function_call_output",
                "call_id": normalize_tool_id(message.tool_call_id),
                "output": message.content,
            })
        else:
            text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            if text:
                items.append({"role": "assistant", "content": text})
            for block in message.content:
                if isinstance(block, ToolCallBlock) and block.complete:
                    items.append({
                        "type": "function_call",
                        "call_id": normalize_tool_id(block.tool_call_id),
                        "name": block.name,
                        "arguments": block.raw_arguments
                        or json.dumps(block.arguments, ensure_ascii=False),
                    })
    return items


def anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush_results() -> None:
        if pending_results:
            result.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for message in messages:
        if isinstance(message, ToolResultMessage):
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": normalize_tool_id(message.tool_call_id, "toolu"),
                "content": message.content,
                "is_error": message.is_error,
            })
            continue
        flush_results()
        if isinstance(message, UserMessage):
            result.append({"role": "user", "content": _user_content(message)})
            continue
        blocks: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ThinkingSummaryBlock):
                # Provider signatures / raw thinking are never carried across turns.
                blocks.append({"type": "text", "text": f"[思考摘要] {block.text}"})
            elif block.complete:
                blocks.append({
                    "type": "tool_use",
                    "id": normalize_tool_id(block.tool_call_id, "toolu"),
                    "name": block.name,
                    "input": block.arguments,
                })
        result.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
    flush_results()
    return result


def google_contents(messages: list[Message]) -> list[dict[str, Any]]:
    """Build stateless GenerateContent history using JSON-compatible SDK objects."""
    result: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, UserMessage):
            result.append({"role": "user", "parts": [{"text": _user_content(message)}]})
        elif isinstance(message, ToolResultMessage):
            try:
                response = json.loads(message.content)
            except (TypeError, ValueError):
                response = {"content": message.content, "is_error": message.is_error}
            result.append({
                "role": "user",
                "parts": [{
                    "function_response": {
                        "name": message.details.get("tool_name", "tool"),
                        "id": normalize_tool_id(message.tool_call_id),
                        "response": response,
                    }
                }],
            })
        else:
            parts: list[dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append({"text": block.text})
                elif isinstance(block, ThinkingSummaryBlock):
                    parts.append({"text": f"[思考摘要] {block.text}"})
                elif block.complete:
                    parts.append({
                        "function_call": {
                            "name": block.name,
                            "id": normalize_tool_id(block.tool_call_id),
                            "args": block.arguments,
                        }
                    })
            result.append({"role": "model", "parts": parts or [{"text": ""}]})
    return result


def openai_tools(tools: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object"}),
                "strict": True,
            },
        }
        for tool in tools
    ]


def responses_tools(tools: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object"}),
            "strict": True,
        }
        for tool in tools
    ]
