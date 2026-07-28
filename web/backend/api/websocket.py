"""WebSocket endpoint for real-time plugin execution progress — multi-engine."""

import asyncio
import functools
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.backend.services.vol_service import get_service

logger = logging.getLogger(__name__)
router = APIRouter()


async def _drain_progress_queue(websocket: WebSocket, progress_queue: asyncio.Queue) -> None:
    """Flush all queued progress messages to client."""
    while True:
        try:
            pmsg = progress_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        await websocket.send_json({"type": "progress", "data": pmsg})


@router.websocket("/ws/plugin")
async def plugin_ws(websocket: WebSocket):
    """Run a plugin and stream progress over WebSocket.

    Client sends:
        {"action": "run",    "plugin": "<name>", "engine": "<id>"}
        {"action": "cancel",                     "engine": "<id>"}
        {"action": "ping"}
    Server sends:
        {"type": "progress|result|error|status|command", "data": ..., "engine": "<id>"}
    """
    await websocket.accept()
    svc = get_service()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "Invalid JSON"})
                continue

            action = msg.get("action")
            engine_id: str = str(msg.get("engine", "vol3") or "vol3").strip() or "vol3"

            if action == "run":
                plugin_name = msg.get("plugin", "").strip()
                if not plugin_name:
                    await websocket.send_json({"type": "error", "data": "Missing plugin name", "engine": engine_id})
                    continue
                os_family = str(msg.get("os_family") or "").strip().lower()
                if os_family in {"linux", "windows"} and not plugin_name.startswith(("linux.", "windows.")):
                    plugin_name = f"{os_family}.{plugin_name}"

                if not svc.is_plugin_available(plugin_name, engine_id=engine_id):
                    await websocket.send_json({
                        "type": "error",
                        "data": f"Plugin not available: {plugin_name}",
                        "engine": engine_id,
                    })
                    continue

                # Extract plugin kwargs: everything except control fields.
                # Includes profile, dump_dir, pid, offset, base, key, regex, etc.
                # force / use_cache control result-cache reuse (not plugin args).
                _CONTROL = {"action", "plugin", "engine", "os_family", "force", "use_cache"}
                plugin_kwargs = {
                    k: v for k, v in msg.items()
                    if k not in _CONTROL and v is not None and v != ""
                }
                use_cache = True
                if msg.get("force") is True or str(msg.get("force", "")).lower() in {"1", "true", "yes"}:
                    use_cache = False
                if "use_cache" in msg:
                    use_cache = bool(msg.get("use_cache"))
                logger.debug(
                    "WS run %s/%s kwargs=%s use_cache=%s",
                    engine_id, plugin_name, list(plugin_kwargs.keys()), use_cache,
                )

                # Echo an equivalent, paste-ready command using the backend's
                # actual Python executable, absolute image path and symbol
                # directories.  The GUI writes this event into its message log.
                try:
                    command = svc.get_manual_plugin_command(
                        plugin_name,
                        engine_id=engine_id,
                        **plugin_kwargs,
                    )
                except Exception:
                    command = None
                    logger.debug("Could not build manual plugin command", exc_info=True)
                if command:
                    await websocket.send_json({
                        "type": "command",
                        "data": command,
                        "engine": engine_id,
                    })

                await websocket.send_json({"type": "status", "data": "running", "engine": engine_id})

                progress_queue: asyncio.Queue = asyncio.Queue()
                loop = asyncio.get_running_loop()

                # Bind the loop/queue for *this* run: if the client disconnects
                # mid-plugin the executor thread outlives this iteration, and a
                # late callback must not push into the next run's queue.
                def progress_cb(message: str, _loop=loop, _queue=progress_queue):
                    _loop.call_soon_threadsafe(_queue.put_nowait, message)

                run_task = loop.run_in_executor(
                    None,
                    functools.partial(
                        svc.run_plugin,
                        plugin_name,
                        progress_callback=progress_cb,
                        engine_id=engine_id,
                        use_cache=use_cache,
                        **plugin_kwargs,
                    ),
                )

                while True:
                    if run_task.done():
                        await asyncio.sleep(0)
                        await _drain_progress_queue(websocket, progress_queue)
                        break
                    try:
                        pmsg = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                        await websocket.send_json({"type": "progress", "data": pmsg, "engine": engine_id})
                        await _drain_progress_queue(websocket, progress_queue)
                    except asyncio.TimeoutError:
                        continue

                try:
                    columns, rows = run_task.result()
                    # Metadata only — full rows are fetched via GET /api/results
                    # to avoid double-shipping large result sets over the socket.
                    await websocket.send_json({
                        "type": "result",
                        "data": {
                            "columns": columns,
                            "total": len(rows),
                            "plugin": plugin_name,
                        },
                        "engine": engine_id,
                    })
                except Exception as exc:
                    await websocket.send_json({"type": "error", "data": str(exc), "engine": engine_id})

                await websocket.send_json({"type": "status", "data": "idle", "engine": engine_id})

            elif action == "cancel":
                try:
                    cancelled = svc.cancel_plugin(engine_id=engine_id)
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "data": str(exc), "engine": engine_id})
                    continue
                status = "cancelled" if cancelled else "idle"
                await websocket.send_json({"type": "status", "data": status, "engine": engine_id})

            elif action == "ping":
                await websocket.send_json({"type": "pong", "data": None})

            else:
                await websocket.send_json({"type": "error", "data": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e, exc_info=True)
