"""Symbol table management API routes."""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from web.backend.services.symbol_service import get_symbol_service

router = APIRouter(prefix="/api/symbols", tags=["symbols"])
logger = logging.getLogger(__name__)


class DownloadSymbolsRequest(BaseModel):
    paths: list[str]


@router.get("/remote")
async def list_remote_symbols(
    query: str = Query(""),
    os: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
):
    return get_symbol_service().list_remote_symbols(
        query=query,
        os_family=os,
        page=page,
        page_size=page_size,
    )


@router.get("/local")
async def list_local_symbols():
    try:
        return get_symbol_service().list_local_symbols()
    except Exception:
        logger.exception("Failed to list local symbols")
        raise HTTPException(500, "Failed to list local symbols")


@router.post("/download")
async def download_symbols(req: DownloadSymbolsRequest):
    if not req.paths:
        raise HTTPException(400, "paths cannot be empty")
    if len(req.paths) > 100:
        raise HTTPException(400, "too many paths, max 100 per request")
    try:
        return get_symbol_service().download_symbols(req.paths)
    except Exception:
        logger.exception("Failed to download symbols")
        raise HTTPException(500, "Failed to download symbols")
