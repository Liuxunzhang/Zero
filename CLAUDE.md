# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Zero is a web-based workbench for **Volatility 3 memory forensics**. It wraps Volatility 3 behind a FastAPI backend and serves a Vue 3 SPA frontend for loading memory images, running plugins, filtering/sorting/exporting results, managing symbol tables, and AI-assisted forensic analysis.

## Commands

```bash
make                    # Full install: Python deps, frontend deps, frontend build
make run                # Production: single-port FastAPI serving built frontend (port 8000)
make test-run           # Development: uvicorn --reload + Vite dev server (ports 8000 / 5173)
make frontend-build     # Rebuild frontend only
make clean              # Remove __pycache__, .pyc, build/, dist/
```

**Frontend-only** (from `web/frontend/`):
```bash
npm run dev             # Vite dev server with HMR (proxy to backend on 8000)
npm run build           # Production build into dist/
```

**Lint/format** (dev deps: `black`, `ruff`):
```bash
black .                 # Format Python code
ruff check .            # Lint Python code
```

There is no test suite yet — the project is at version 0.1.0 and test infrastructure is planned but not implemented.

## Architecture

### Backend: layered engine abstraction

```
FastAPI routes (web/backend/api/)
    │
    ▼
Service layer (web/backend/services/)   — thin wrappers, module-level singletons
    │
    ▼
EngineManager (zero/engines/manager.py) — singleton registry, one instance per engine_id
    │
    ▼
EngineBase adapter (zero/engines/)      — abstract interface for any forensic engine
    │
    ▼
VolatilityWrapper (zero/core/wrapper.py) — raw vol3 API calls, plugin process management
```

**Key design decisions:**

- **Multi-engine abstraction** (`zero/engines/base.py`): `EngineBase` defines a uniform interface (load image, list plugins, run plugin, get results, export). Only `Vol3Engine` exists today, but the design supports adding other engines. The web layer never touches engine internals directly — all access goes through `EngineManager`.
- **EngineManager singleton** (`zero/engines/manager.py`): Lazy-initialized on first `get_manager()` call. Holds one adapter instance per `engine_id` with fully independent state (image, plugin, results, cache). Engines are registered via `register_factory()`, allowing deferred construction.
- **Plugin process isolation** (`zero/core/plugin_worker.py`): Plugins run in a **separate process** — `multiprocessing.Process` on Linux (`fork`), `subprocess.Popen` on macOS (`spawn`, to avoid fork-safety issues with vol3's native libraries). This enables hard cancellation and timeout enforcement. Communication via `multiprocessing.Queue` or JSON-over-stdout.
- **Disk-backed result cache** (`zero/core/result_cache.py`): CSV files under `saved_results/vol3/{image_name}/{plugin_name}.csv`. In-memory LRU query cache (max 64 entries). Cache survives restarts via `ENABLE_DISK_CACHE` config.
- **All config is centralized** in `zero/config.py` — timeouts, paths, AI settings, cache strategy, feature flags. No magic values scattered through the codebase.
- **Services are module-level singletons**: `EngineService`, `AiService`, `SymbolService` in `web/backend/services/` — each is a thin wrapper initialized at import time, exposing methods that the API routes call.
- **Advanced filter expression parser** (`zero/utils/filter_expression.py`): Custom tokenizer/evaluator supporting 11 operators (`-eq`, `-contain`, `-match`, `-startswith`, etc.) with `&&`/`||` logic. Evaluated server-side against cached results.

### Frontend: Vue 3 SPA with Pinia

Single Pinia store (`stores/app.js`) holds all global state. Components are direct consumers of the store. API calls go through `api/index.js` (Axios REST client). Plugin execution uses WebSocket at `/ws/plugin` for real-time progress streaming.

### AI integration

Multi-provider AI assistant (`web/backend/services/ai_service.py`) supporting OpenAI-compatible APIs (siliconflow, openai, deepseek, ollama, baishanyun). Features: SSE streaming chat, compressed conversation memory (LLM-generated summaries), structured memory items with keyword retrieval, prompt library, persistent conversation store under `.zero/ai/conversations/`.

### Symbol table management

`scripts/import_symbols.sh` is the unified entry point for generating Volatility 3 symbol tables (ISF JSON) from distro debug packages. Supports Ubuntu 22/24, Debian 13, CentOS 6/7/8. Generated files land in `symbols/` and are auto-detected by the backend on each plugin run — no restart needed.

## Key paths

| Path | Purpose |
|---|---|
| `zero/config.py` | All tunable constants |
| `zero/engines/base.py` | `EngineBase` abstract class + `EngineResult` dataclass |
| `zero/engines/manager.py` | `EngineManager` singleton — entry point for all engine access |
| `zero/core/wrapper.py` | `VolatilityWrapper` — core vol3 integration (largest file) |
| `web/backend/main.py` | FastAPI app creation and route registration |
| `web/backend/api/routes.py` | REST endpoints (engines, images, plugins, results, export, cache) |
| `web/backend/api/websocket.py` | WebSocket for plugin execution progress |
| `web/backend/api/ai_routes.py` | AI chat, profiles, prompts, conversation management |
| `web/frontend/src/stores/app.js` | Pinia store — all frontend global state |
| `plugin_categories.json` | User-editable plugin category grouping |
| `dev/api.md` | REST API reference (Chinese) |
