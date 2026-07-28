"""Strict tool registry, result handles and cancellation-aware execution."""

from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import jsonschema

from zero.utils.filter_expression import AdvancedFilter

from .models import ToolResultMessage
from .storage import safe_id


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
ToolExecutor = Callable[[dict[str, Any], "ToolExecutionContext"], Awaitable[Any] | Any]
Hook = Callable[[Any, "ToolExecutionContext"], Awaitable[Any] | Any]


@dataclass(slots=True)
class ToolExecutionContext:
    run_id: str
    conversation_id: str
    engine_id: str
    image_id: str
    tool_call_id: str
    progress: ProgressCallback | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def report(self, **data: Any) -> None:
        if self.progress:
            result = self.progress(data)
            if inspect.isawaitable(result):
                await result


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: ToolExecutor
    execution_mode: Literal["sequential", "parallel"] = "sequential"
    risk: Literal["read", "write", "extract"] = "read"
    timeout_seconds: float = 600.0
    idempotent: bool = True
    preflight: Hook | None = None
    postprocess: Hook | None = None

    def declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "execution_mode": self.execution_mode,
            "risk": self.risk,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._sequential_locks: dict[str, asyncio.Lock] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        # Validate the schema itself at registration time.
        jsonschema.Draft202012Validator.check_schema(definition.input_schema)
        self._tools[definition.name] = definition

    def declarations(self) -> list[dict[str, Any]]:
        return [tool.declaration() for tool in self._tools.values()]

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        context: ToolExecutionContext,
        *,
        raw_arguments: str = "",
        call_complete: bool = True,
    ) -> ToolResultMessage:
        details = {"tool_name": name, "tool_call_id": context.tool_call_id}
        if not call_complete:
            return ToolResultMessage(
                context.tool_call_id,
                "工具调用因输出 token 截断而不完整，已禁止执行。",
                details=details,
                is_error=True,
            )
        tool = self._tools.get(name)
        if tool is None:
            return ToolResultMessage(
                context.tool_call_id,
                f"未知工具: {name}",
                details=details,
                is_error=True,
            )
        if arguments is None:
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, ValueError):
                arguments = None
        if not isinstance(arguments, dict):
            return ToolResultMessage(
                context.tool_call_id,
                "工具参数不是有效 JSON object。",
                details=details,
                is_error=True,
            )
        try:
            jsonschema.Draft202012Validator(tool.input_schema).validate(arguments)
        except jsonschema.ValidationError as exc:
            return ToolResultMessage(
                context.tool_call_id,
                f"工具参数校验失败: {exc.message}",
                details={**details, "validation_path": list(exc.absolute_path)},
                is_error=True,
            )
        try:
            if tool.preflight:
                checked = tool.preflight(arguments, context)
                arguments = await checked if inspect.isawaitable(checked) else checked
            if context.cancel_event.is_set():
                raise asyncio.CancelledError

            async def invoke() -> Any:
                result = tool.executor(arguments, context)
                return await result if inspect.isawaitable(result) else result

            async def timed() -> Any:
                return await asyncio.wait_for(invoke(), timeout=tool.timeout_seconds)

            if tool.execution_mode == "sequential":
                lock = self._sequential_locks.setdefault(context.engine_id, asyncio.Lock())
                async with lock:
                    result = await timed()
            else:
                result = await timed()
            if tool.postprocess:
                processed = tool.postprocess(result, context)
                result = await processed if inspect.isawaitable(processed) else processed
            content = json.dumps(result, ensure_ascii=False, default=str)
            if isinstance(result, dict):
                details.update({
                    key: result[key]
                    for key in ("plugin", "result_id", "total", "columns")
                    if key in result
                })
            return ToolResultMessage(
                context.tool_call_id,
                content,
                details=details,
                is_error=False,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return ToolResultMessage(
                context.tool_call_id,
                f"工具执行超时（{tool.timeout_seconds:g} 秒）。",
                details=details,
                is_error=True,
            )
        except Exception as exc:
            return ToolResultMessage(
                context.tool_call_id,
                f"工具执行失败: {exc}",
                details=details,
                is_error=True,
            )


class ResultHandleStore:
    """Conversation-scoped immutable snapshots addressed by opaque result IDs."""

    PREVIEW_CHARS = 12_000

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _directory(self, image_id: str, conversation_id: str) -> Path:
        return self.root / safe_id(image_id or "unknown") / safe_id(conversation_id)

    def _path(self, image_id: str, conversation_id: str, result_id: str) -> Path:
        return self._directory(image_id, conversation_id) / f"{safe_id(result_id)}.json.gz"

    def save(
        self,
        image_id: str,
        conversation_id: str,
        *,
        plugin: str,
        columns: list[str],
        rows: list[Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result_id = f"res_{uuid.uuid4().hex}"
        payload = {
            "result_id": result_id,
            "plugin": plugin,
            "columns": list(columns),
            "rows": [list(row) for row in rows],
            "total": len(rows),
            "metadata": metadata or {},
        }
        path = self._path(image_id, conversation_id, result_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), default=str)
        preview_rows: list[list[Any]] = []
        used = 0
        for row in payload["rows"]:
            encoded = json.dumps(row, ensure_ascii=False, default=str)
            if used + len(encoded) > self.PREVIEW_CHARS:
                break
            preview_rows.append(row)
            used += len(encoded)
        return {
            "result_id": result_id,
            "plugin": plugin,
            "columns": payload["columns"],
            "total": payload["total"],
            "preview": preview_rows,
            "preview_truncated": len(preview_rows) < len(rows),
        }

    def _load(self, image_id: str, conversation_id: str, result_id: str) -> dict[str, Any]:
        path = self._path(image_id, conversation_id, result_id)
        if not path.exists():
            raise KeyError("result handle not found in this image/conversation")
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("result_id") != result_id:
            raise ValueError("corrupt result handle")
        return payload

    def query(
        self,
        image_id: str,
        conversation_id: str,
        result_id: str,
        *,
        filter_text: str = "",
        sort_column: str = "",
        sort_desc: bool = False,
        columns: list[str] | None = None,
        page: int = 1,
        page_size: int = 200,
    ) -> dict[str, Any]:
        payload = self._load(image_id, conversation_id, result_id)
        source_columns = [str(value) for value in payload.get("columns", [])]
        rows = [tuple(row) for row in payload.get("rows", [])]
        if filter_text:
            advanced = AdvancedFilter(source_columns)
            parsed = advanced.set_expression(filter_text)
            if advanced.get_error():
                raise ValueError(advanced.get_error())
            if parsed:
                rows = advanced.filter_rows(rows)
            else:
                needle = filter_text.casefold()
                rows = [row for row in rows if any(needle in str(cell).casefold() for cell in row)]
        indexes = {name.casefold(): index for index, name in enumerate(source_columns)}
        if sort_column:
            index = indexes.get(sort_column.casefold())
            if index is None:
                raise ValueError(f"unknown sort column: {sort_column}")
            rows.sort(key=lambda row: (row[index] is None, str(row[index]).casefold()), reverse=sort_desc)
        selected_columns = columns or source_columns
        selected_indexes = []
        for name in selected_columns:
            index = indexes.get(str(name).casefold())
            if index is None:
                raise ValueError(f"unknown selected column: {name}")
            selected_indexes.append(index)
        size = max(1, min(200, int(page_size)))
        page = max(1, int(page))
        total = len(rows)
        start = (page - 1) * size
        selected_rows = [[row[index] for index in selected_indexes] for row in rows[start:start + size]]
        return {
            "result_id": result_id,
            "plugin": payload.get("plugin", ""),
            "columns": selected_columns,
            "rows": selected_rows,
            "total": total,
            "page": page,
            "page_size": size,
            "total_pages": max(1, math.ceil(total / size)),
        }
