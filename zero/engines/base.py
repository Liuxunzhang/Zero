"""Abstract base interface that every forensic engine adapter must implement.

Design principles:
- Each engine maintains fully independent state (image, plugin, results, cache).
- The Engine Manager holds one instance per engine_id; the Web layer never
  touches engine internals directly.
- All methods that can block should be called from a thread executor by the
  caller; engines themselves are *not* required to be async-safe internally.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class EngineResult:
    """Uniform result container returned by every engine after a plugin run."""

    columns: List[str]
    rows: List[Tuple]
    plugin: str
    engine_id: str
    error: Optional[str] = None
    total: int = field(init=False)

    def __post_init__(self) -> None:
        self.total = len(self.rows)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": [list(r) for r in self.rows],
            "total": self.total,
            "plugin": self.plugin,
            "engine_id": self.engine_id,
        }


class EngineBase(abc.ABC):
    """Abstract engine adapter interface.

    Subclasses must implement every abstract method.  All state (current image,
    running plugin, result set, disk cache) must be kept *inside* the subclass
    and must NOT be shared with other engine instances.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def engine_id(self) -> str:
        """Unique, stable, lower-case identifier, e.g. 'vol3'."""

    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in the UI, e.g. 'Volatility 3'."""

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def load_image(self, path: str) -> bool:
        """Load a memory image.  Returns True on success."""

    @abc.abstractmethod
    def get_image_status(self) -> Dict[str, Any]:
        """Return a dict with keys: loaded, path, current_plugin, plugin_busy."""

    # ------------------------------------------------------------------
    # Plugin catalogue
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_plugins(self, os_family: str = "linux") -> Dict[str, List[str]]:
        """Return {category: [plugin_name, ...]} for the given OS family."""

    @abc.abstractmethod
    def is_plugin_available(self, plugin_name: str, os_family: Optional[str] = None) -> bool:
        """Return True if the plugin can be run by this engine."""

    def resolve_plugin_name(self, plugin_name: str) -> str:
        """Resolve a short or UI-side name to the engine's internal plugin name."""
        return plugin_name

    # ------------------------------------------------------------------
    # Plugin execution (blocking; call from a thread executor)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def run_plugin(
        self,
        plugin_name: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> Tuple[List[str], List[Tuple]]:
        """Run *plugin_name* and return (columns, rows).

        *progress_callback* is called with a string message whenever the engine
        has progress to report.  It must be thread-safe (called from the engine
        thread, consumed by the async event loop).
        """

    @abc.abstractmethod
    def cancel_plugin(self) -> bool:
        """Request cancellation of the currently running plugin.

        Returns True if a cancellation was actually issued.
        """

    # ------------------------------------------------------------------
    # Plugin management
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def reload_plugins(self) -> int:
        """Re-scan plugin directories.  Returns total number of plugins found."""

    # ------------------------------------------------------------------
    # Results retrieval (with server-side filter / sort / page)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_results(
        self,
        filter_text: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_desc: bool = False,
        page: int = 1,
        page_size: int = 200,
    ) -> Dict[str, Any]:
        """Return a paginated, filtered, sorted view of the last plugin result.

        Returned dict keys: columns, rows, total, page, page_size,
        total_pages, current_plugin.
        """

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def clear_cache(self, plugin_name: Optional[str] = None) -> None:
        """Clear cached results.  *plugin_name=None* clears everything."""

    @abc.abstractmethod
    def get_cache_stats(self) -> Dict[str, Any]:
        """Return engine-specific cache statistics."""

    # ------------------------------------------------------------------
    # Export (optional — default raises NotImplementedError)
    # ------------------------------------------------------------------

    def export_results(
        self,
        fmt: str = "csv",
        filter_text: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_desc: bool = False,
    ) -> Optional[str]:
        """Export the latest results (optionally the filtered/sorted view).

        Returns the written file path.
        """
        raise NotImplementedError(f"{self.engine_id()} does not support export")

    def get_current_result_context(self, max_rows: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Return a dict suitable for AI context, or None if no results."""
        return None
