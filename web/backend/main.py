"""Zero Web — FastAPI application entry point."""

import logging
import sys
from pathlib import Path

# Ensure project root is importable so `zero.*` works.
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
    uvicorn.run(
        "web.backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parent)],
    )


if __name__ == "__main__":
    main()
