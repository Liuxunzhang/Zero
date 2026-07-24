"""Zero Web — FastAPI application entry point."""

import logging
import os
import sys
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
from web.backend.api.routes import router as api_router
from web.backend.api.websocket import router as ws_router
from web.backend.api.ai_routes import router as ai_router
from web.backend.api.symbol_routes import router as symbol_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

app = FastAPI(title="Zero Web", version="0.1.0")

# CORS — allow Vite dev server during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _ApiTokenMiddleware(BaseHTTPMiddleware):
    """Optional shared-token gate when config.API_TOKEN is non-empty."""

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

        if provided != token:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


app.add_middleware(_ApiTokenMiddleware)

app.include_router(api_router)
app.include_router(ws_router)
app.include_router(ai_router)
app.include_router(symbol_router)

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
