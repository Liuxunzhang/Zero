"""Persistent Agent Runtime APIs and reconnectable SSE."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from web.backend.ai_runtime.service import get_runtime

router = APIRouter(prefix="/api/ai")


class CreateRunRequest(BaseModel):
    message: str
    engine_id: str = "vol3"
    mode: str = "agent"
    include_context: bool = True
    max_turns: int = Field(12, ge=1, le=100)
    max_tool_calls: int = Field(20, ge=0, le=200)
    max_seconds: int = Field(1800, ge=1, le=86400)


class CompactRequest(BaseModel):
    pass


@router.post("/conversations/{conversation_id}/runs", status_code=202)
async def create_run(conversation_id: str, request: CreateRunRequest):
    runtime = get_runtime()
    if runtime.conversations.get(conversation_id) is None:
        raise HTTPException(404, "Conversation not found")
    try:
        return runtime.create_run(
            conversation_id,
            message=request.message,
            engine_id=request.engine_id,
            mode=request.mode,
            include_context=request.include_context,
            max_turns=request.max_turns,
            max_tool_calls=request.max_tool_calls,
            max_seconds=request.max_seconds,
        )
    except ValueError as exc:
        raise HTTPException(409 if "pinned" in str(exc) else 400, str(exc))


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = get_runtime().runs.get(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, after_seq: int = Query(0, ge=0)):
    runtime = get_runtime()
    if runtime.runs.get(run_id) is None:
        raise HTTPException(404, "Run not found")

    async def stream():
        async for event in runtime.runs.subscribe(run_id, after_seq):
            yield f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    runtime = get_runtime()
    if runtime.runs.get(run_id) is None:
        raise HTTPException(404, "Run not found")
    cancelled = await runtime.runs.cancel(run_id)
    return {"ok": cancelled, "run_id": run_id}


@router.post("/conversations/{conversation_id}/compact")
async def compact_conversation(conversation_id: str, _request: CompactRequest | None = None):
    runtime = get_runtime()
    if runtime.conversations.get(conversation_id) is None:
        raise HTTPException(404, "Conversation not found")
    return await runtime.compact(conversation_id)


@router.get("/conversations/{conversation_id}/context")
async def conversation_context(conversation_id: str):
    runtime = get_runtime()
    if runtime.conversations.get(conversation_id) is None:
        raise HTTPException(404, "Conversation not found")
    return runtime.context_stats(conversation_id)


@router.get("/tool-results/{result_id}")
async def query_tool_result(
    result_id: str,
    conversation_id: str,
    filter: str = "",
    sort_column: str = "",
    sort_desc: bool = False,
    columns: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=200),
):
    runtime = get_runtime()
    meta = runtime.conversations.get(conversation_id)
    if meta is None:
        raise HTTPException(404, "Conversation not found")
    selected = [part.strip() for part in columns.split(",") if part.strip()] or None
    try:
        return runtime.results.query(
            str(meta.get("image_id") or "unknown"),
            conversation_id,
            result_id,
            filter_text=filter,
            sort_column=sort_column,
            sort_desc=sort_desc,
            columns=selected,
            page=page,
            page_size=page_size,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
