"""EngineManager — central registry and dispatcher for all forensic engines.

Usage
-----
    from zero.engines.manager import get_manager

    mgr = get_manager()
    engine = mgr.get_engine("vol3")          # raises KeyError if unknown
    engine.load_image("/path/to/dump.raw")
    cols, rows = engine.run_plugin("windows.pslist.PsList")

The manager is a module-level singleton initialised lazily on first call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from zero.engines.base import EngineBase

logger = logging.getLogger(__name__)

_MANAGER: Optional["EngineManager"] = None


@dataclass
class _EngineRegistration:
    engine_id: str
    display_name: str
    factory: Optional[Callable[[], EngineBase]] = None
    instance: Optional[EngineBase] = None
    status: Optional[Dict[str, Any]] = None

    @property
    def loaded(self) -> bool:
        return self.instance is not None


class EngineManager:
    """Registry of engine adapters; each engine keeps its own independent state."""

    def __init__(self) -> None:
        self._engines: Dict[str, _EngineRegistration] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, engine: EngineBase) -> None:
        """Register an engine.  Replaces any existing engine with the same id."""
        eid = engine.engine_id()
        self._engines[eid] = _EngineRegistration(
            engine_id=eid,
            display_name=engine.display_name(),
            instance=engine,
        )
        logger.info("Registered engine: %s (%s)", eid, engine.display_name())

    def register_factory(
        self,
        engine_id: str,
        display_name: str,
        factory: Callable[[], EngineBase],
        status: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register an engine factory without constructing the engine immediately."""
        self._engines[engine_id] = _EngineRegistration(
            engine_id=engine_id,
            display_name=display_name,
            factory=factory,
            status=status or {},
        )
        logger.info("Registered lazy engine: %s (%s)", engine_id, display_name)

    def unregister(self, engine_id: str) -> None:
        self._engines.pop(engine_id, None)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_engine(self, engine_id: str) -> EngineBase:
        """Return the engine for *engine_id*.  Raises ValueError if unknown."""
        registration = self._engines.get(engine_id)
        if registration is None:
            available = ", ".join(self._engines) or "(none)"
            raise ValueError(
                f"Unknown engine '{engine_id}'. Available: {available}"
            )
        if registration.instance is None:
            if registration.factory is None:
                raise ValueError(f"Engine '{engine_id}' has no factory")
            logger.info("Initializing engine: %s", engine_id)
            registration.instance = registration.factory()
        return registration.instance

    def list_engines(self) -> List[Dict[str, Any]]:
        """Return metadata for all registered engines."""
        result = []
        for eid, registration in self._engines.items():
            status = self.get_image_status(eid)
            result.append({
                "engine_id": eid,
                "display_name": registration.display_name,
                "image_loaded": status.get("loaded", False),
                "image_path": status.get("path"),
                "plugin_busy": status.get("plugin_busy", False),
                "current_plugin": status.get("current_plugin"),
                "available": status.get("available", True),
                "initialized": registration.loaded,
            })
        return result

    def has_engine(self, engine_id: str) -> bool:
        return engine_id in self._engines

    # ------------------------------------------------------------------
    # Convenience pass-throughs (all require explicit engine_id)
    # ------------------------------------------------------------------

    def load_image(self, engine_id: str, path: str) -> bool:
        return self.get_engine(engine_id).load_image(path)

    def get_image_status(self, engine_id: str) -> Dict[str, Any]:
        registration = self._engines.get(engine_id)
        if registration is None:
            return self.get_engine(engine_id).get_image_status()
        if registration.instance is not None:
            return registration.instance.get_image_status()
        status = dict(registration.status or {})
        status.setdefault("loaded", False)
        status.setdefault("path", None)
        status.setdefault("current_plugin", None)
        status.setdefault("plugin_busy", False)
        status.setdefault("os_family", "linux")
        status.setdefault("available", True)
        status.setdefault("initialized", False)
        return status

    def list_plugins(self, engine_id: str, os_family: str = "linux") -> Dict[str, List[str]]:
        return self.get_engine(engine_id).list_plugins(os_family)

    def is_plugin_available(self, engine_id: str, plugin_name: str) -> bool:
        return self.get_engine(engine_id).is_plugin_available(plugin_name)

    def run_plugin(self, engine_id: str, plugin_name: str, progress_callback=None, **kwargs):
        return self.get_engine(engine_id).run_plugin(
            plugin_name, progress_callback=progress_callback, **kwargs
        )

    def cancel_plugin(self, engine_id: str) -> bool:
        return self.get_engine(engine_id).cancel_plugin()

    def reload_plugins(self, engine_id: str) -> int:
        return self.get_engine(engine_id).reload_plugins()

    def get_results(self, engine_id: str, **kwargs) -> Dict[str, Any]:
        registration = self._engines.get(engine_id)
        if registration is not None and registration.instance is None:
            page = int(kwargs.get("page", 1) or 1)
            page_size = int(kwargs.get("page_size", 200) or 200)
            return {
                "columns": [],
                "rows": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "current_plugin": None,
            }
        return self.get_engine(engine_id).get_results(**kwargs)

    def clear_cache(self, engine_id: str, plugin_name: Optional[str] = None) -> None:
        return self.get_engine(engine_id).clear_cache(plugin_name)

    def get_cache_stats(self, engine_id: str) -> Dict[str, Any]:
        registration = self._engines.get(engine_id)
        if registration is not None and registration.instance is None:
            return {}
        return self.get_engine(engine_id).get_cache_stats()

    def export_results(self, engine_id: str, fmt: str = "csv", **kwargs: Any) -> Optional[str]:
        return self.get_engine(engine_id).export_results(fmt, **kwargs)

    def get_current_result_context(
        self, engine_id: str, max_rows: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        return self.get_engine(engine_id).get_current_result_context(max_rows)

    def update_engine_settings(self, engine_id: str, **settings: Any) -> Dict[str, Any]:
        engine = self.get_engine(engine_id)
        updater = getattr(engine, "set_profile", None)
        if "profile" in settings and callable(updater):
            return updater(settings["profile"])
        raise ValueError(f"Engine '{engine_id}' does not support runtime settings update")


# ---------------------------------------------------------------------------
# Module-level singleton factory
# ---------------------------------------------------------------------------

def _build_manager() -> EngineManager:
    """Instantiate and register the Volatility 3 engine."""
    mgr = EngineManager()

    def _make_vol3() -> EngineBase:
        from zero.engines.vol3_engine import Vol3Engine
        return Vol3Engine()

    mgr.register_factory(
        "vol3",
        "Volatility 3",
        _make_vol3,
        status={"available": True, "os_family": "linux"},
    )
    return mgr


def get_manager() -> EngineManager:
    """Return the singleton EngineManager, creating it on first call."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = _build_manager()
    return _MANAGER
