"""Volatility 3 engine adapter.

Wraps the existing VolatilityWrapper (zero/core/wrapper.py) behind the
unified EngineBase interface.  All state is kept inside this instance;
multiple Vol3Engine instances are fully independent.
"""

from __future__ import annotations

import logging
import shlex
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from zero.engines.base import EngineBase
from zero.utils.exporter import ResultExporter
from zero.utils.filter_expression import AdvancedFilter

try:
    from zero import config as _zero_config
    _DEFAULT_QUERY_CACHE_MAX = int(getattr(_zero_config, "RESULTS_QUERY_CACHE_MAX", 64))
    _DEFAULT_QUERY_CACHE_MAX_ROWS = int(
        getattr(_zero_config, "RESULTS_QUERY_CACHE_MAX_ROWS", 2_000_000)
    )
    # 0 / None means "no limit".
    _MAX_EXPORT_ROWS = int(getattr(_zero_config, "MAX_TABLE_ROWS", 0) or 0)
except Exception:
    _DEFAULT_QUERY_CACHE_MAX = 64
    _DEFAULT_QUERY_CACHE_MAX_ROWS = 2_000_000
    _MAX_EXPORT_ROWS = 0

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
    requires_modal = is_dump or has_required

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
        from zero.core.wrapper import VolatilityWrapper
        self._wrapper = VolatilityWrapper()
        self._lock = threading.RLock()
        self._image_path: Optional[str] = None
        self._image_loaded: bool = False
        self._current_plugin: Optional[str] = None
        self._plugin_busy: bool = False
        self._columns: List[str] = []
        self._rows: List[Tuple] = []
        self._results_version: int = 0
        # Filtered+sorted result sets (not per-page). True LRU via OrderedDict.
        self._fs_cache: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
        self._results_query_cache_max_entries: int = _DEFAULT_QUERY_CACHE_MAX
        self._results_query_cache_max_rows: int = _DEFAULT_QUERY_CACHE_MAX_ROWS
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

    def resolve_plugin_name(self, plugin_name: str) -> str:
        return self._wrapper.resolve_plugin_name(plugin_name)

    def get_manual_plugin_command(
        self, plugin_name: str, **kwargs: Any
    ) -> Optional[str]:
        """Build the exact standalone worker command used by this engine."""
        if not self._image_loaded:
            return None
        resolved = self._wrapper.resolve_plugin_name(plugin_name)
        argv = self._wrapper._build_worker_command(resolved, kwargs)
        return shlex.join(str(value) for value in argv)

    def _resolve_plugin_class(self, plugin_name: str) -> Tuple[Optional[str], Any]:
        """Return (resolved_name, plugin_class); (name, None) when not installed."""
        resolved = self._wrapper.resolve_plugin_name(plugin_name)
        try:
            from volatility3 import framework
            return resolved, framework.list_plugins().get(resolved)
        except Exception:
            return resolved, None

    def get_plugin_metadata(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Introspect vol3 plugin requirements and return PluginArgDef-compatible metadata."""
        try:
            resolved, plugin_class = self._resolve_plugin_class(plugin_name)
            if plugin_class is None:
                return None
            return _build_vol3_plugin_metadata(resolved, plugin_class)
        except Exception:
            return None

    def get_plugin_docs(self, plugin_name: str) -> Dict[str, Any]:
        """Documentation for the plugin help panel, from the vol3 class itself.

        Shape matches what PluginParamsModal renders: purpose / key_params / notes.
        Returns {} when the plugin is unknown or carries no docstring.
        """
        try:
            resolved, plugin_class = self._resolve_plugin_class(plugin_name)
            if plugin_class is None:
                return {}

            purpose = " ".join((plugin_class.__doc__ or "").split()).strip()
            key_params: Dict[str, str] = {}
            try:
                for req in plugin_class.get_requirements():
                    if type(req).__name__ in _SKIP_REQ_TYPES:
                        continue
                    description = (getattr(req, "description", "") or "").strip()
                    if description:
                        key_params[req.name] = description
            except Exception:
                pass

            if not purpose and not key_params:
                return {}
            doc: Dict[str, Any] = {"notes": f"Volatility 3 插件: {resolved}"}
            if purpose:
                doc["purpose"] = purpose
            if key_params:
                doc["key_params"] = key_params
            return doc
        except Exception:
            return {}

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
                # Keep references; wrapper cache owns the lists (read-only to callers).
                self._columns = columns
                self._rows = rows
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

    def apply_runtime_settings(self) -> None:
        """Apply the allowlisted WebUI settings to this live engine instance."""
        from zero import config as runtime_config

        global _MAX_EXPORT_ROWS
        with self._lock:
            self._wrapper.apply_runtime_settings()
            self._results_query_cache_max_entries = max(
                1,
                int(getattr(runtime_config, "RESULTS_QUERY_CACHE_MAX", 64)),
            )
            self._results_query_cache_max_rows = max(
                1000,
                int(
                    getattr(
                        runtime_config,
                        "RESULTS_QUERY_CACHE_MAX_ROWS",
                        2_000_000,
                    )
                ),
            )
            _MAX_EXPORT_ROWS = max(
                0,
                int(getattr(runtime_config, "MAX_TABLE_ROWS", 10000) or 0),
            )
            self._trim_results_query_cache()

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
        self._fs_cache.clear()

    def _get_filtered_sorted(
        self,
        cols: List[str],
        rows: List[Tuple],
        results_version: int,
        filter_text: Optional[str],
        sort_column: Optional[str],
        sort_desc: bool,
    ) -> Tuple[List[str], List[Tuple], int]:
        """Return (columns, filtered_sorted_rows, total) with LRU cache.

        Cache key excludes page/page_size so pagination only slices.
        """
        has_filter = bool(filter_text and filter_text.strip())
        has_sort = bool(sort_column and sort_column in cols)

        # Plain pagination: nothing to recompute, so neither copy nor cache the
        # row list — callers only slice it.
        if not has_filter and not has_sort:
            return cols, rows, len(rows)

        fs_key = (
            results_version,
            filter_text or "",
            sort_column or "",
            bool(sort_desc),
        )
        with self._lock:
            cached = self._fs_cache.get(fs_key)
            if cached is not None:
                self._fs_cache.move_to_end(fs_key)
                return cached["columns"], cached["rows"], cached["total"]

        work_rows = rows
        # True once work_rows is a list we own and may sort in place.
        owns_rows = False
        if has_filter:
            af = AdvancedFilter(cols)
            if af.set_expression(filter_text):
                work_rows = af.filter_rows(work_rows)
            else:
                # Simple substring: short-circuit per row (avoid join).
                fl = filter_text.lower()
                work_rows = [
                    r for r in work_rows
                    if any(fl in str(c).lower() for c in r)
                ]
            owns_rows = True

        if has_sort:
            idx = cols.index(sort_column)
            sort_key = self._sort_key

            def key_fn(r):
                return sort_key(r[idx] if idx < len(r) else "")

            if owns_rows:
                work_rows.sort(key=key_fn, reverse=sort_desc)
            else:
                # Never sort the engine's canonical row list in place.
                work_rows = sorted(work_rows, key=key_fn, reverse=sort_desc)

        total = len(work_rows)
        entry = {"columns": cols, "rows": work_rows, "total": total}
        with self._lock:
            self._fs_cache[fs_key] = entry
            self._fs_cache.move_to_end(fs_key)
            self._trim_results_query_cache()
        return cols, work_rows, total

    def _trim_results_query_cache(self) -> None:
        """Evict LRU-first until within both the entry and total-row budgets.

        Caller must hold ``self._lock``. The entry cap alone is not enough: 64
        cached filter results over a million-row plugin output would each pin a
        full row list.
        """
        cache = self._fs_cache
        while len(cache) > self._results_query_cache_max_entries and len(cache) > 1:
            cache.popitem(last=False)
        cached_rows = sum(entry["total"] for entry in cache.values())
        while cached_rows > self._results_query_cache_max_rows and len(cache) > 1:
            _key, evicted = cache.popitem(last=False)
            cached_rows -= evicted["total"]

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
            # Share row list reference for filtering; filter path copies as needed.
            rows = self._rows
            current_plugin = self._current_plugin
            results_version = self._results_version

        cols, work_rows, total = self._get_filtered_sorted(
            cols, rows, results_version, filter_text, sort_column, sort_desc
        )

        page = max(1, int(page or 1))
        page_size = max(1, int(page_size or 200))
        start = (page - 1) * page_size
        end = start + page_size
        page_rows = work_rows[start:end]

        return {
            "columns": cols,
            "rows": [list(r) for r in page_rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "current_plugin": current_plugin,
        }

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def clear_cache(self, plugin_name: Optional[str] = None) -> None:
        self._wrapper.clear_cache(plugin_name)
        with self._lock:
            if plugin_name is None:
                self._columns = []
                self._rows = []
                self._current_plugin = None
            # Always drop filter/sort page cache after any cache clear.
            self._invalidate_results_query_cache()

    def get_cache_stats(self) -> Dict[str, Any]:
        return self._wrapper.get_cache_stats()

    # ------------------------------------------------------------------
    # Export / AI context
    # ------------------------------------------------------------------

    def export_results(
        self,
        fmt: str = "csv",
        filter_text: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_desc: bool = False,
    ) -> Optional[str]:
        with self._lock:
            if not self._columns or not self._rows:
                return None
            plugin_name = self._current_plugin or "export"
            columns = list(self._columns)
            rows = self._rows
            results_version = self._results_version

        # Export what the user is looking at, not the raw result set. Reuses
        # the filter/sort LRU, so a view the user already browsed is a cache hit.
        is_view = bool(filter_text and filter_text.strip()) or bool(
            sort_column and sort_column in columns
        )
        if is_view:
            columns, rows, _total = self._get_filtered_sorted(
                columns, rows, results_version, filter_text, sort_column, sort_desc
            )
            plugin_name = f"{plugin_name}_filtered"
        rows = list(rows)
        if 0 < _MAX_EXPORT_ROWS < len(rows):
            logging.warning(
                "Export of %s truncated to MAX_TABLE_ROWS=%d of %d rows",
                plugin_name,
                _MAX_EXPORT_ROWS,
                len(rows),
            )
            rows = rows[:_MAX_EXPORT_ROWS]
        return ResultExporter.auto_export(columns, rows, plugin_name, fmt=fmt)

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
