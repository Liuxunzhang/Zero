"""Volatility 3 engine adapter.

Wraps the existing VolatilityWrapper (lexzero/core/wrapper.py) behind the
unified EngineBase interface.  All state is kept inside this instance;
multiple Vol3Engine instances are fully independent.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from lexzero.engines.base import EngineBase
from lexzero.utils.exporter import ResultExporter
from lexzero.utils.filter_expression import AdvancedFilter

# Internal requirement types to skip (infrastructure, not user params).
_SKIP_REQ_TYPES = frozenset({
    "ModuleRequirement", "VersionRequirement", "TranslationLayerRequirement",
    "SymbolTableRequirement", "PluginRequirement",
})

# Vol3 plugins that write extracted files and need a dump directory.
_DUMP_PLUGIN_NAMES = frozenset({
    "windows.dumpfiles.DumpFiles",
    "windows.pedump.PEDump",
    "windows.memmap.Memmap",
    "windows.vadyarascan.VadYaraScan",
    "linux.malfind.Malfind",
    "windows.malfind.Malfind",
})


def _build_vol3_plugin_metadata(resolved_name: str, plugin_class) -> Dict[str, Any]:
    """Convert vol3 plugin requirements into a PluginArgDef-compatible metadata dict."""
    try:
        from volatility3.framework.configuration import requirements as reqs
    except ImportError:
        return _empty_metadata(resolved_name)

    _TYPE_MAP = {
        "IntRequirement":     ("int",    "整数值"),
        "BooleanRequirement": ("bool",   ""),
        "StringRequirement":  ("string", "字符串"),
        "ListRequirement":    ("string", "逗号分隔值"),
        "URIRequirement":     ("string", "URI"),
        "BytesRequirement":   ("string", "Bytes"),
    }

    args: List[Dict[str, Any]] = []
    for req in plugin_class.get_requirements():
        type_name = type(req).__name__
        if type_name in _SKIP_REQ_TYPES:
            continue
        optional = getattr(req, "optional", True)
        arg_type, placeholder_hint = _TYPE_MAP.get(type_name, ("string", ""))
        args.append({
            "name": req.name,
            "flag": f"--{req.name}",
            "arg_type": arg_type,
            "required": not optional,
            "default": None,
            "placeholder": placeholder_hint,
            "help": getattr(req, "description", "") or "",
        })

    # Add dump_dir for plugins that extract files.
    is_dump = resolved_name in _DUMP_PLUGIN_NAMES
    if is_dump:
        args.append({
            "name": "dump_dir",
            "flag": "--dump-dir",
            "arg_type": "path",
            "required": False,
            "default": "",
            "placeholder": "dumps/vol3",
            "help": "提取文件的保存目录（默认: dumps/vol3）",
        })

    has_required = any(a["required"] for a in args)
    requires_modal = is_dump

    return {
        "args": args,
        "has_args": bool(args),
        "has_required_args": has_required,
        "requires_modal": requires_modal,
        "arg_names": [a["name"] for a in args],
    }


def _empty_metadata(resolved_name: str) -> Dict[str, Any]:
    return {
        "args": [],
        "has_args": False,
        "has_required_args": False,
        "requires_modal": False,
        "arg_names": [],
    }


class Vol3Engine(EngineBase):
    """Engine adapter for Volatility 3."""

    def __init__(self) -> None:
        # Import lazily so that missing vol3 doesn't crash the whole app.
        from lexzero.core.wrapper import VolatilityWrapper
        self._wrapper = VolatilityWrapper()
        self._lock = threading.RLock()
        self._image_path: Optional[str] = None
        self._image_loaded: bool = False
        self._current_plugin: Optional[str] = None
        self._plugin_busy: bool = False
        self._columns: List[str] = []
        self._rows: List[Tuple] = []
        self._results_version: int = 0
        self._results_query_cache: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        self._results_query_cache_max_entries: int = 64
        self._os_family: str = "linux"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def engine_id(self) -> str:
        return "vol3"

    def display_name(self) -> str:
        return "Volatility 3"

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    def load_image(self, path: str) -> bool:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Image file not found: {path}")
        success = self._wrapper.load_image(str(p))
        if success:
            with self._lock:
                self._image_path = str(p)
                self._image_loaded = True
                self._columns = []
                self._rows = []
                self._current_plugin = None
                self._plugin_busy = False
                self._invalidate_results_query_cache()
        return success

    def get_image_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "loaded": self._image_loaded,
                "path": self._image_path,
                "current_plugin": self._current_plugin,
                "plugin_busy": self._plugin_busy,
                "os_family": self._os_family,
            }

    # ------------------------------------------------------------------
    # Plugin catalogue
    # ------------------------------------------------------------------

    def list_plugins(self, os_family: str = "linux") -> Dict[str, List[str]]:
        with self._lock:
            self._os_family = os_family
        return self._wrapper.get_plugin_categories(os_family)

    def is_plugin_available(self, plugin_name: str, os_family: Optional[str] = None) -> bool:
        return self._wrapper.is_plugin_available(plugin_name)

    def get_plugin_metadata(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Introspect vol3 plugin requirements and return PluginArgDef-compatible metadata."""
        try:
            from volatility3 import framework
            import volatility3.plugins
            plugin_map = framework.list_plugins()
            resolved = self._wrapper._resolve_plugin_name(plugin_name)
            plugin_class = plugin_map.get(resolved)
            if plugin_class is None:
                return None
            return _build_vol3_plugin_metadata(resolved, plugin_class)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Plugin execution
    # ------------------------------------------------------------------

    def run_plugin(
        self,
        plugin_name: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> Tuple[List[str], List[Tuple]]:
        with self._lock:
            if not self._image_loaded:
                raise ValueError("No memory image loaded")
            if self._plugin_busy:
                raise ValueError("Another plugin is already running")
            self._plugin_busy = True
            self._current_plugin = plugin_name

        try:
            columns, rows = self._wrapper.run_plugin(
                plugin_name,
                progress_callback=progress_callback,
                **kwargs,
            )
            with self._lock:
                self._columns = list(columns)
                self._rows = list(rows)
                self._invalidate_results_query_cache()
            return columns, rows
        finally:
            with self._lock:
                self._plugin_busy = False

    def cancel_plugin(self) -> bool:
        return self._wrapper.terminate_running_plugin(force=True)

    # ------------------------------------------------------------------
    # Plugin management
    # ------------------------------------------------------------------

    def reload_plugins(self) -> int:
        return self._wrapper.reload_plugins()

    # ------------------------------------------------------------------
    # Results retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def _sort_key(value: Any) -> Tuple[int, Any]:
        if value is None:
            return (2, "")
        if isinstance(value, bool):
            return (0, int(value))
        if isinstance(value, (int, float)):
            return (0, value)
        text = str(value).strip()
        if text:
            sign = 1
            if text[0] in {"+", "-"}:
                if text[0] == "-":
                    sign = -1
                text_body = text[1:]
            else:
                text_body = text
            if text_body.isdigit():
                return (0, sign * int(text_body))
        return (1, str(value).lower())

    def _invalidate_results_query_cache(self) -> None:
        self._results_version += 1
        self._results_query_cache.clear()

    def get_results(
        self,
        filter_text: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_desc: bool = False,
        page: int = 1,
        page_size: int = 200,
    ) -> Dict[str, Any]:
        with self._lock:
            cols = list(self._columns)
            rows = list(self._rows)
            current_plugin = self._current_plugin
            results_version = self._results_version

        query_key = (
            results_version,
            filter_text or "",
            sort_column or "",
            bool(sort_desc),
            int(page),
            int(page_size),
        )
        with self._lock:
            cached = self._results_query_cache.get(query_key)
        if cached is not None:
            return {k: (list(v) if isinstance(v, list) else v) for k, v in cached.items()}

        if filter_text and filter_text.strip():
            af = AdvancedFilter(cols)
            if af.set_expression(filter_text):
                rows = af.filter_rows(rows)
            else:
                fl = filter_text.lower()
                rows = [r for r in rows if fl in "|".join(str(c).lower() for c in r)]

        total = len(rows)

        if sort_column and sort_column in cols:
            idx = cols.index(sort_column)
            rows.sort(
                key=lambda r: self._sort_key(r[idx] if idx < len(r) else ""),
                reverse=sort_desc,
            )

        start = (page - 1) * page_size
        end = start + page_size
        page_rows = rows[start:end]

        result = {
            "columns": cols,
            "rows": [list(r) for r in page_rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "current_plugin": current_plugin,
        }
        with self._lock:
            self._results_query_cache[query_key] = result
            if len(self._results_query_cache) > self._results_query_cache_max_entries:
                self._results_query_cache.pop(next(iter(self._results_query_cache)))
        return result

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def clear_cache(self, plugin_name: Optional[str] = None) -> None:
        self._wrapper.clear_cache(plugin_name)
        if plugin_name is None:
            with self._lock:
                self._columns = []
                self._rows = []
                self._current_plugin = None
                self._invalidate_results_query_cache()

    def get_cache_stats(self) -> Dict[str, Any]:
        return self._wrapper.get_cache_stats()

    # ------------------------------------------------------------------
    # Export / AI context
    # ------------------------------------------------------------------

    def export_results(self, fmt: str = "csv") -> Optional[str]:
        with self._lock:
            if not self._columns or not self._rows:
                return None
            plugin_name = self._current_plugin or "export"
            columns = list(self._columns)
            rows = list(self._rows)
        return ResultExporter.auto_export(columns, rows, plugin_name, format=fmt)

    def get_current_result_context(self, max_rows: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._columns or not self._rows:
                return None
            total = len(self._rows)
            if max_rows is None:
                rows_copy = [list(r) for r in self._rows]
            else:
                rows_copy = [list(r) for r in self._rows[: max(1, int(max_rows))]]
            return {
                "plugin": self._current_plugin or "unknown",
                "columns": list(self._columns),
                "rows": rows_copy,
                "total": total,
            }

    def get_current_columns(self) -> List[str]:
        with self._lock:
            return list(self._columns)
