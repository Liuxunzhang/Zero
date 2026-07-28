"""Zero Web — FastAPI application entry point."""

import hmac
import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Ensure project root is importable so `zero.*` works.
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from zero import config as zero_config
# Apply the persisted WebUI overlay before any engine/service can be created.
from web.backend.services.settings_service import load_runtime_settings

load_runtime_settings()

from web.backend.api.routes import router as api_router
from web.backend.api.websocket import router as ws_router
from web.backend.api.ai_routes import router as ai_router
from web.backend.api.runtime_routes import router as ai_runtime_router
from web.backend.api.symbol_routes import router as symbol_router
from web.backend.api.settings_routes import router as settings_router
from web.backend.ai_runtime.service import get_runtime

_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def _configure_logging() -> None:
    """Apply config.LOG_LEVEL / LOG_FILE.

    Error messages already tell users to check LOG_FILE (see
    ``VolatilityWrapper._diagnose_error``), so the file has to actually exist.
    """
    level_name = str(getattr(zero_config, "LOG_LEVEL", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=_LOG_FORMAT)

    log_file = str(getattr(zero_config, "LOG_FILE", "") or "").strip()
    if not log_file:
        return

    log_path = Path(log_file).expanduser()
    if not log_path.is_absolute():
        log_path = Path(_project_root) / log_path
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=int(getattr(zero_config, "LOG_MAX_BYTES", 5 * 1024 * 1024)),
            backupCount=int(getattr(zero_config, "LOG_BACKUP_COUNT", 3)),
            encoding="utf-8",
        )
    except OSError as e:
        logging.warning("Could not open log file %s: %s (console logging only)", log_path, e)
        return
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.setLevel(level)
    logging.getLogger().addHandler(handler)
    logging.info("Logging to %s at level %s", log_path, level_name)


_configure_logging()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Performs legacy migration, event retention cleanup and interrupted-run
    # recovery without issuing any provider request.
    get_runtime()
    yield


app = FastAPI(title="Zero Web", version="0.1.0", lifespan=_lifespan)

# CORS — allow Vite dev server during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _ApiTokenMiddleware(BaseHTTPMiddleware):
    """Optional shared-token gate when config.API_TOKEN is non-empty.

    The token must be ASCII: HTTP header values are latin-1 per RFC 9110, so a
    non-ASCII token can never round-trip through X-API-Token / Authorization.
    ``_warn_if_token_unusable`` surfaces that at startup instead of silently
    rejecting every request.
    """

    _PUBLIC_PREFIXES = ("/api/health",)

    async def dispatch(self, request: Request, call_next):
        token = str(getattr(zero_config, "API_TOKEN", "") or "").strip()
        if not token:
            return await call_next(request)

        path = request.url.path or ""
        # Static frontend assets stay open; API/WS require token.
        if not path.startswith("/api") and not path.startswith("/ws"):
            return await call_next(request)
        if any(path.startswith(p) for p in self._PUBLIC_PREFIXES):
            return await call_next(request)

        provided = request.headers.get("X-API-Token") or ""
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        if not provided:
            provided = request.query_params.get("token") or ""

        # Constant-time compare; encode first so non-ASCII tokens cannot raise.
        if not hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8")):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


def _warn_if_token_unusable() -> None:
    token = str(getattr(zero_config, "API_TOKEN", "") or "").strip()
    if token and not token.isascii():
        logging.warning(
            "API_TOKEN contains non-ASCII characters; it cannot be sent via the "
            "X-API-Token or Authorization headers (header values are latin-1). "
            "Use an ASCII-only token."
        )


_warn_if_token_unusable()
app.add_middleware(_ApiTokenMiddleware)

app.include_router(api_router)
app.include_router(ws_router)
app.include_router(ai_router)
app.include_router(ai_runtime_router)
app.include_router(symbol_router)
app.include_router(settings_router)

# Serve built frontend in production (if dist/ exists).
_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")


def main():
    import uvicorn

    host = os.environ.get("ZERO_BIND_HOST") or getattr(zero_config, "BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("ZERO_BIND_PORT") or getattr(zero_config, "BIND_PORT", 8000))
    uvicorn.run(
        "web.backend.main:app",
        host=host,
        port=port,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parent)],
    )


if __name__ == "__main__":
    main()
