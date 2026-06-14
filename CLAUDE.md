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

**Tests** (dev dep: `pytest`):
```bash
make test               # install dev deps + run the pure-function test suite
pytest tests/           # run tests directly
```

The suite covers pure functions (filter expression, memory store, conversation store, AI message construction) but not the engine or network layer.

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

Multi-provider AI assistant (`web/backend/services/ai_service.py`) supporting OpenAI-compatible APIs (siliconflow, openai, deepseek, ollama, baishanyun). Features: SSE streaming chat, agent tool-calling loop (`run_plugin` / `list_plugins` / `dump_process` / `dump_pe`), compressed conversation memory (LLM-generated summaries, debounced to one compression every 3 turns or 60s), structured memory items with keyword retrieval, prompt library.

**Conversation history** has a single source of truth: `conversation_store` under `.zero/ai/conversations/`. The service is stateless w.r.t. history — `_build_messages` reads from the store on each `/chat` call. `GET /api/ai/history` is gone; the frontend loads history via `GET /api/ai/conversations/{id}`.

**Agent token budget**: tool results folded back into the agent `messages` list are compacted (`_compact_tool_result_for_messages`, 30-row digest) so a multi-step tool chain doesn't balloon the prompt. The freshly-executed result is still surfaced to the model in full via the `tool_result` SSE event summary.

**Runtime config persistence**: profile metadata and settings live exclusively under `.zero/ai/` (`profiles.json`, `settings.json`). `config.py` is **never rewritten** at runtime — it only holds static defaults. The `persist_to_config_py` flag still exists for compatibility but now only mirrors values into the in-memory `config` module, not the file.

### Plugin result cache

Volatility plugin results are cached both in memory (`VolatilityWrapper._cache`) and on disk (`DiskResultCache`, CSV under `saved_results/vol3/{image}/`). **The cache key incorporates plugin kwargs** (pid/offset/key/…) so that `handles.Handles pid=4376` and `handles.Handles pid=100` get distinct entries — without this, different parameters would collide and return wrong data.

- `dump_dir` and `dump` kwargs are deliberately excluded from the key (they're per-run paths on the `use_cache=False` dump paths).
- `use_cache=False` now gates **writes** too, not just reads — dump runs don't pollute the cache.
- `clear_cache(plugin)` purges **all** kwargs variants of a plugin (in-memory + on-disk); `clear_cache(None)` wipes the whole image's cache.

### Agent tool-call protocols

The agent loop (`chat_stream`) supports two tool-call transports:

1. **Standard OpenAI** (`delta.tool_calls` deltas) — the default for gpt-4o and most providers.
2. **DSML in-band text** — some models (notably `deepseek-v4-pro`) emit tool calls as `<｜｜DSML｜｜invoke name="...">` markup inside `delta.content`. `DSMLStreamParser` (`web/backend/services/dsml_parser.py`) strips this markup before it reaches the user and feeds parsed calls into the same execution path. If a stream contains no DSML, the parser is zero-overhead.

### Tests

```bash
make test   # install dev deps (pytest) + run the suite
```

Pure-function tests in `tests/` cover `filter_expression`, `memory_store`, `conversation_store`, AI message construction (`_build_messages`, `_compact_tool_result_for_messages`), the memory-compression debounce, the plugin result cache (kwargs-aware key isolation, `use_cache` write-skip, prefix-clear), and the DSML in-band tool-call parser. There is no engine/network integration test suite yet — the engine layer (`Vol3Engine`, `VolatilityWrapper`) is not thread-safe for concurrent plugin runs.

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
