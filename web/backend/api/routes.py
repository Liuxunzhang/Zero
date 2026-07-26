"""REST API routes for Zero Web."""

import logging
import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from web.backend.services.vol_service import get_service

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# Project root (for resolving dumps/ dir).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Simple time-based rate limiting for mutating endpoints.
_RELOAD_MIN_INTERVAL = 10.0
_CACHE_CLEAR_MIN_INTERVAL = 5.0
_last_reload_at: float = 0.0
_last_cache_clear_at: float = 0.0
_IMAGE_LIST_CACHE_TTL_SECONDS = 10.0
_image_list_cache_expires_at: float = 0.0
_image_list_cache_payload: Optional[dict] = None

_IMAGE_EXTENSIONS = {
    ".raw", ".mem", ".dmp", ".vmem", ".img", ".bin",
    ".lime", ".elf", ".core", ".crash", ".hpak", ".aff4",
}


# ── Request / Response models ──────────────────────────────────────

class ImageLoadRequest(BaseModel):
    path: str
    engine: str = "vol3"


class PluginRunRequest(BaseModel):
    plugin: str
    engine: str = "vol3"


_ALLOWED_EXPORT_FORMATS = {"csv", "json", "txt"}


class ExportRequest(BaseModel):
    format: str = "csv"
    engine: str = "vol3"


class CacheClearRequest(BaseModel):
    plugin: Optional[str] = None
    engine: str = "vol3"


class EngineSettingsRequest(BaseModel):
    profile: Optional[str] = None


# ── Engines ────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    """Lightweight health check that must not initialize forensic engines."""
    return {"ok": True}


@router.get("/engines")
async def list_engines():
    """Return metadata for all registered engines."""
    return {"engines": get_service().list_engines()}


@router.get("/engines/{engine_id}/settings")
async def get_engine_settings(engine_id: str):
    try:
        status = get_service().get_image_status(engine_id=engine_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "engine": engine_id,
        "settings": {
            "profile": status.get("profile", ""),
            "suggested_profiles": status.get("suggested_profiles", []),
            "available": status.get("available", True),
        },
    }


@router.post("/engines/{engine_id}/settings")
async def update_engine_settings(engine_id: str, req: EngineSettingsRequest):
    payload = req.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(400, "No settings provided")
    try:
        status = get_service().update_engine_settings(engine_id=engine_id, **payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "engine": engine_id, "status": status}


# ── Image ──────────────────────────────────────────────────────────

def _image_path_allowed(resolved: Path) -> bool:
    """If ALLOW_IMAGE_PATHS is configured, require path under a whitelist root."""
    try:
        from zero import config as zero_config
        roots = getattr(zero_config, "ALLOW_IMAGE_PATHS", None)
    except Exception:
        roots = None
    if not roots:
        return True
    resolved_str = str(resolved)
    for root in roots:
        try:
            root_path = Path(root).expanduser().resolve()
        except OSError:
            continue
        if resolved_str == str(root_path) or resolved_str.startswith(str(root_path) + "/"):
            return True
        # Windows-style prefix safety is not required on this stack.
        if resolved_str.startswith(str(root_path) + "\\"):
            return True
    return False


@router.post("/image/load")
async def load_image(req: ImageLoadRequest):
    p = Path(req.path).expanduser().resolve()
    if p.suffix.lower() not in _IMAGE_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file extension '{p.suffix}'. "
            f"Allowed: {', '.join(sorted(_IMAGE_EXTENSIONS))}",
        )
    if not _image_path_allowed(p):
        raise HTTPException(
            403,
            "Image path is outside ALLOW_IMAGE_PATHS whitelist",
        )
    svc = get_service()
    try:
        success = svc.load_image(req.path, engine_id=req.engine)
        if not success:
            raise HTTPException(400, "Failed to load image")
        return {"ok": True, "path": req.path, "engine": req.engine}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        logger.exception("Unexpected error while loading image: %s", req.path)
        raise HTTPException(500, "Internal error while loading image")


@router.get("/image/status")
async def image_status(engine: str = Query("vol3")):
    try:
        return get_service().get_image_status(engine_id=engine)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/image/list")
async def list_images(refresh: bool = Query(False)):
    """List memory image files in the dumps/ directory."""
    global _image_list_cache_expires_at, _image_list_cache_payload
    now = time.monotonic()
    if not refresh and _image_list_cache_payload is not None and now < _image_list_cache_expires_at:
        return _image_list_cache_payload

    dumps_dir = (_PROJECT_ROOT / "dumps").resolve()
    files = []
    if dumps_dir.exists() and dumps_dir.is_dir():
        for f in sorted(dumps_dir.rglob("*")):
            # Resolve symlinks and reject entries that escape dumps_dir.
            try:
                resolved = f.resolve()
            except OSError:
                continue
            if not str(resolved).startswith(str(dumps_dir)):
                continue
            if resolved.is_file() and resolved.suffix.lower() in _IMAGE_EXTENSIONS:
                size_mb = resolved.stat().st_size / (1024 * 1024)
                files.append({
                    "name": resolved.name,
                    "path": str(resolved),
                    "size": f"{size_mb:.1f} MB",
                    "relative": str(resolved.relative_to(_PROJECT_ROOT.resolve())),
                })
    payload = {"dumps_dir": str(dumps_dir), "files": files}
    _image_list_cache_payload = payload
    _image_list_cache_expires_at = now + _IMAGE_LIST_CACHE_TTL_SECONDS
    return payload


# ── Plugins ────────────────────────────────────────────────────────

@router.get("/plugins/{os_family}")
async def list_plugins(os_family: str, engine: str = Query("vol3")):
    if os_family not in ("linux", "windows"):
        raise HTTPException(400, "os_family must be 'linux' or 'windows'")
    try:
        categories = get_service().get_plugin_categories(os_family, engine_id=engine)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"os_family": os_family, "categories": categories, "engine": engine}


@router.get("/plugin-args/{plugin_name}")
async def get_plugin_args(plugin_name: str, engine: str = Query("vol3")):
    """Return parameter metadata for a Volatility 3 plugin."""
    meta = get_service().get_plugin_metadata(plugin_name, engine_id=engine) or {}
    return {
        "plugin": plugin_name,
        "engine": engine,
        "args": meta.get("args", []),
        "requires_modal": meta.get("requires_modal", False),
        "has_required_args": meta.get("has_required_args", False),
        "arg_names": meta.get("arg_names", []),
    }


@router.get("/plugin-docs/{plugin_name}")
async def get_plugin_docs(plugin_name: str, engine: str = Query("vol3")):
    """Return plugin documentation derived from the engine's plugin class."""
    doc = get_service().get_plugin_docs(plugin_name, engine_id=engine)
    return {"plugin": plugin_name, "engine": engine, "doc": doc}


@router.post("/plugins/reload")
async def reload_plugins(engine: str = Query("vol3")):
    """Re-scan plugin directories (including custom plugins/) and reload."""
    global _last_reload_at
    elapsed = time.monotonic() - _last_reload_at
    if elapsed < _RELOAD_MIN_INTERVAL:
        raise HTTPException(
            429,
            f"Too many requests. Please wait {_RELOAD_MIN_INTERVAL - elapsed:.0f}s before reloading again.",
        )
    _last_reload_at = time.monotonic()
    try:
        count = get_service().reload_plugins(engine_id=engine)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "plugin_count": count, "engine": engine}


# ── Results ────────────────────────────────────────────────────────

@router.get("/results")
async def get_results(
    filter: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    desc: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=5000),
    engine: str = Query("vol3"),
):
    try:
        return get_service().get_results(
            filter_text=filter,
            sort_column=sort,
            sort_desc=desc,
            page=page,
            page_size=page_size,
            engine_id=engine,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Export ──────────────────────────────────────────────────────────

@router.post("/export")
async def export_results(req: ExportRequest):
    if req.format not in _ALLOWED_EXPORT_FORMATS:
        raise HTTPException(
            400,
            f"Unsupported export format '{req.format}'. "
            f"Allowed: {', '.join(sorted(_ALLOWED_EXPORT_FORMATS))}",
        )
    try:
        filepath = get_service().export_results(req.format, engine_id=req.engine)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not filepath:
        raise HTTPException(400, "No data to export")
    return {"ok": True, "path": filepath}


# ── Cache ──────────────────────────────────────────────────────────

@router.delete("/cache")
async def clear_cache(req: CacheClearRequest = CacheClearRequest()):
    global _last_cache_clear_at
    elapsed = time.monotonic() - _last_cache_clear_at
    if elapsed < _CACHE_CLEAR_MIN_INTERVAL:
        raise HTTPException(
            429,
            f"Too many requests. Please wait {_CACHE_CLEAR_MIN_INTERVAL - elapsed:.0f}s before clearing cache again.",
        )
    _last_cache_clear_at = time.monotonic()
    try:
        get_service().clear_cache(req.plugin, engine_id=req.engine)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/cache/stats")
async def cache_stats(engine: str = Query("vol3")):
    try:
        return get_service().get_cache_stats(engine_id=engine)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Plugin cancel ──────────────────────────────────────────────────

@router.delete("/plugin/cancel")
async def cancel_plugin(engine: str = Query("vol3")):
    try:
        cancelled = get_service().cancel_plugin(engine_id=engine)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "cancelled": cancelled}
