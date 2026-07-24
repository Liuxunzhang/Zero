"""Web service layer — multi-engine adapter for FastAPI.

Wraps EngineManager and provides the same API that routes.py expects,
now with an explicit ``engine_id`` parameter on every method.

Backward-compatible shim:
    ``get_service()`` returns an ``EngineService`` proxy; existing code that
    only uses vol3 continues to work unchanged (engine_id defaults to 'vol3').
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from zero.engines.manager import get_manager

logger = logging.getLogger(__name__)


class EngineService:
    """Thin wrapper around EngineManager; stateless — just delegates calls."""

    def __init__(self):
        self._manager = get_manager()

    # -- Engine catalogue ---------------------------------------------------

    def list_engines(self) -> List[Dict[str, Any]]:
        return self._manager.list_engines()

    def update_engine_settings(
        self, engine_id: str = "vol3", **settings: Any
    ) -> Dict[str, Any]:
        return self._manager.update_engine_settings(engine_id, **settings)

    # -- Image --------------------------------------------------------------

    def load_image(self, path: str, engine_id: str = "vol3") -> bool:
        return self._manager.load_image(engine_id, path)

    @property
    def image_status(self) -> Dict[str, Any]:
        """Backward-compat: returns vol3 status."""
        return self._manager.get_image_status("vol3")

    def get_image_status(self, engine_id: str = "vol3") -> Dict[str, Any]:
        return self._manager.get_image_status(engine_id)

    # -- Plugin catalogue ---------------------------------------------------

    def get_plugin_categories(
        self, os_family: str = "linux", engine_id: str = "vol3"
    ) -> Dict[str, List[str]]:
        return self._manager.list_plugins(engine_id, os_family)

    def is_plugin_available(
        self, plugin_name: str, engine_id: str = "vol3"
    ) -> bool:
        return self._manager.is_plugin_available(engine_id, plugin_name)

    # -- Plugin execution ---------------------------------------------------

    def run_plugin(
        self,
        plugin_name: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        engine_id: str = "vol3",
        use_cache: bool = True,
        **kwargs: Any,
    ) -> Tuple[List[str], List]:
        return self._manager.run_plugin(
            engine_id,
            plugin_name,
            progress_callback,
            use_cache=use_cache,
            **kwargs,
        )

    def cancel_plugin(self, engine_id: str = "vol3") -> bool:
        return self._manager.cancel_plugin(engine_id)

    # -- Results ------------------------------------------------------------

    def get_results(
        self,
        filter_text: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_desc: bool = False,
        page: int = 1,
        page_size: int = 200,
        engine_id: str = "vol3",
    ) -> Dict[str, Any]:
        return self._manager.get_results(
            engine_id,
            filter_text=filter_text,
            sort_column=sort_column,
            sort_desc=sort_desc,
            page=page,
            page_size=page_size,
        )

    def get_current_result_context(
        self, max_rows: Optional[int] = None, engine_id: str = "vol3"
    ) -> Optional[Dict[str, Any]]:
        return self._manager.get_current_result_context(engine_id, max_rows)

    def get_current_columns(self, engine_id: str = "vol3") -> List[str]:
        eng = self._manager.get_engine(engine_id)
        if hasattr(eng, "get_current_columns"):
            return eng.get_current_columns()
        result = self.get_results(engine_id=engine_id, page_size=1)
        return result.get("columns", [])

    # -- Export -------------------------------------------------------------

    def export_results(self, fmt: str = "csv", engine_id: str = "vol3") -> Optional[str]:
        return self._manager.export_results(engine_id, fmt)

    # -- Cache --------------------------------------------------------------

    def reload_plugins(self, engine_id: str = "vol3") -> int:
        return self._manager.reload_plugins(engine_id)

    def clear_cache(
        self, plugin_name: Optional[str] = None, engine_id: str = "vol3"
    ) -> None:
        return self._manager.clear_cache(engine_id, plugin_name)

    def get_cache_stats(self, engine_id: str = "vol3") -> Dict[str, Any]:
        return self._manager.get_cache_stats(engine_id)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: Optional["EngineService"] = None


def get_service() -> EngineService:
    global _service
    if _service is None:
        _service = EngineService()
    return _service


# Backward-compat alias
VolService = EngineService
