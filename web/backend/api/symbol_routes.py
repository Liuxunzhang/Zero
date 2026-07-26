"""Symbol table management API routes.

Handlers that touch GitHub or the filesystem are deliberately declared ``def``
rather than ``async def``: SymbolService uses blocking ``requests`` calls (up to
45 s for an index refresh, minutes for a bulk download). Declared sync, FastAPI
runs them in its threadpool, so they cannot stall the event loop — and with it
every other request plus the plugin-progress WebSocket.
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from web.backend.services.symbol_service import get_symbol_service

router = APIRouter(prefix="/api/symbols", tags=["symbols"])
logger = logging.getLogger(__name__)


class DownloadSymbolsRequest(BaseModel):
    paths: list[str]
    repo: str = Field("", description="GitHub owner/name, e.g. Abyss-W4tcher/volatility3-symbols")


@router.get("/repos")
async def list_symbol_repos():
    return get_symbol_service().list_repos()


@router.get("/remote")
def list_remote_symbols(
    query: str = Query(""),
    os: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    repo: str = Query("", description="GitHub owner/name of the symbol index"),
    force_refresh: bool = Query(False, description="Bypass soft TTL and re-fetch from GitHub"),
):
    try:
        return get_symbol_service().list_remote_symbols(
            query=query,
            os_family=os,
            page=page,
            page_size=page_size,
            repo=repo,
            force_refresh=force_refresh,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        logger.exception("Failed to list remote symbols")
        raise HTTPException(500, "Failed to list remote symbols")


@router.get("/local")
def list_local_symbols():
    try:
        return get_symbol_service().list_local_symbols()
    except Exception:
        logger.exception("Failed to list local symbols")
        raise HTTPException(500, "Failed to list local symbols")


@router.post("/download")
def download_symbols(req: DownloadSymbolsRequest):
    if not req.paths:
        raise HTTPException(400, "paths cannot be empty")
    if len(req.paths) > 100:
        raise HTTPException(400, "too many paths, max 100 per request")
    try:
        return get_symbol_service().download_symbols(req.paths, repo=req.repo)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        logger.exception("Failed to download symbols")
        raise HTTPException(500, "Failed to download symbols")
