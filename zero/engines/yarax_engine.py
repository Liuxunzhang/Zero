"""YARA-X engine adapter with cancellable subprocess scans."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import multiprocessing
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from zero.engines.base import EngineBase
from zero.utils.exporter import ResultExporter
from zero.utils.filter_expression import AdvancedFilter
from zero.yarax.compiler import compile_package, normalize_globals
from zero.yarax.errors import (
    RuleCompileError,
    ScanCancelledError,
    ScanFailedError,
    ScanTimeoutError,
)
from zero.yarax.store import YaraXStore, get_yarax_store

logger = logging.getLogger(__name__)

COLUMNS = [
    "Package", "PackageVersion", "Rule", "Namespace", "Tags", "Metadata",
    "Pattern", "Offset", "Length", "XorKey",
]


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _scan_worker(payload: dict[str, Any], event_queue) -> None:
    """Compile/load rules and scan one image. Runs in an isolated process."""
    try:
        import yara_x

        root = Path(payload["package_root"])
        compiled_path = Path(payload["compiled_path"])
        event_queue.put({"type": "progress", "data": "正在准备 YARA-X 规则…"})
        if compiled_path.is_file():
            try:
                with compiled_path.open("rb") as handle:
                    rules = yara_x.Rules.deserialize_from(handle)
            except Exception:
                compiled_path.unlink(missing_ok=True)
                rules = None
        else:
            rules = None
        if rules is None:
            rules, _ = compile_package(
                root,
                payload["manifest"],
                relaxed_regex=bool(payload.get("relaxed_regex")),
                output_path=compiled_path,
            )
        scanner = yara_x.Scanner(rules)
        scanner.set_timeout(int(payload["timeout"]))
        scanner.max_matches_per_pattern(int(payload["max_matches_per_pattern"]))
        scanner.fast_scan(bool(payload.get("fast_scan", False)))
        manifest_values, _ = normalize_globals(payload["manifest"].get("globals") or {})
        runtime_globals = payload.get("globals") or {}
        for name, value in runtime_globals.items():
            if name not in manifest_values:
                raise ValueError(f"Unknown package global: {name}")
            scanner.set_global(name, value)
        event_queue.put({"type": "progress", "data": "正在扫描镜像…"})
        results = scanner.scan_file(payload["image_path"])
        rows: list[list[Any]] = []
        truncated = False
        limit = int(payload["max_result_rows"])
        package_name = payload["package_name"]
        package_version = str(payload["package_version"])
        for rule in results.matching_rules:
            tags = ",".join(rule.tags)
            metadata = json.dumps(
                _json_safe(dict(rule.metadata)), ensure_ascii=False, sort_keys=True,
            )
            produced = False
            for pattern in rule.patterns:
                for match in pattern.matches:
                    produced = True
                    if len(rows) >= limit:
                        truncated = True
                        break
                    rows.append([
                        package_name, package_version, rule.identifier, rule.namespace,
                        tags, metadata, pattern.identifier, int(match.offset),
                        int(match.length), match.xor_key,
                    ])
                if truncated:
                    break
            if not produced and not truncated:
                if len(rows) >= limit:
                    truncated = True
                else:
                    rows.append([
                        package_name, package_version, rule.identifier, rule.namespace,
                        tags, metadata, "", None, None, None,
                    ])
            if truncated:
                break
        event_queue.put({
            "type": "result", "columns": COLUMNS, "rows": rows,
            "truncated": truncated, "status": "truncated" if truncated else "success",
        })
    except Exception as exc:
        try:
            import yara_x
            if isinstance(exc, yara_x.TimeoutError):
                event_queue.put({"type": "error", "status": "timeout", "error": str(exc)})
                return
        except Exception:
            pass
        event_queue.put({
            "type": "error", "status": "compile_error"
            if exc.__class__.__name__ == "ValidationError" else "scan_error",
            "error": str(exc),
            "diagnostics": getattr(exc, "diagnostics", []),
        })


class YaraXEngine(EngineBase):
    def __init__(self, store: YaraXStore | None = None):
        self._store = store or get_yarax_store()
        self._lock = threading.RLock()
        self._image_path: str | None = None
        self._image_loaded = False
        self._current_plugin: str | None = None
        self._plugin_busy = False
        self._process = None
        self._cancel_requested = False
        self._columns: list[str] = []
        self._rows: list[tuple] = []
        self._status = "idle"
        self._truncated = False
        self._diagnostics: list[dict[str, Any]] = []
        self._cache_hit = False

    def engine_id(self) -> str:
        return "yarax"

    def display_name(self) -> str:
        return "YARA-X"

    def load_image(self, path: str) -> bool:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Image file not found: {resolved}")
        with self._lock:
            self._image_path = str(resolved)
            self._image_loaded = True
            self._current_plugin = None
            self._columns = []
            self._rows = []
            self._status = "idle"
            self._truncated = False
        return True

    def get_image_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "loaded": self._image_loaded, "path": self._image_path,
                "current_plugin": self._current_plugin,
                "plugin_busy": self._plugin_busy, "os_family": "all",
                "available": True, "status": self._status,
                "truncated": self._truncated,
            }

    def list_plugins(self, os_family: str = "linux") -> Dict[str, List[str]]:
        return {
            "规则包": [package["plugin"] for package in self._store.enabled_packages()]
        }

    def is_plugin_available(self, plugin_name: str, os_family: Optional[str] = None) -> bool:
        if not plugin_name.startswith("package."):
            return False
        package_id = plugin_name[8:]
        return any(package["id"] == package_id for package in self._store.enabled_packages())

    def get_plugin_metadata(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        if not self.is_plugin_available(plugin_name):
            return None
        package = self._store.get_package(plugin_name[8:])
        version = self._store.get_version(package["id"], package["active_version_id"])
        args = [
            {
                "name": "timeout", "flag": "--timeout", "arg_type": "int",
                "required": False, "default": 300, "placeholder": "300",
                "help": "扫描超时（秒）",
            },
            {
                "name": "max_matches_per_pattern", "flag": "--max-matches-per-pattern",
                "arg_type": "int", "required": False, "default": 1000,
                "placeholder": "1000", "help": "单个 Pattern 最大匹配数",
            },
        ]
        for name, spec in (version["manifest"].get("globals") or {}).items():
            args.append({
                "name": name, "flag": f"--{name}", "arg_type": spec.get("type", "string"),
                "required": False, "default": spec.get("default"),
                "placeholder": str(spec.get("default", "")), "help": "YARA 外部变量",
            })
        return {
            "args": args, "has_args": bool(args), "has_required_args": False,
            "requires_modal": bool(version["manifest"].get("globals")),
            "arg_names": [item["name"] for item in args],
        }

    def get_plugin_docs(self, plugin_name: str) -> Dict[str, Any]:
        if not self.is_plugin_available(plugin_name):
            return {}
        package = self._store.get_package(plugin_name[8:])
        version = self._store.get_version(package["id"], package["active_version_id"])
        return {
            "title": package["name"], "description": package["description"],
            "version": version["version"], "license": version["manifest"].get("license", ""),
            "homepage": version["manifest"].get("homepage", ""),
            "entrypoints": version["manifest"].get("entrypoints", []),
        }

    @staticmethod
    def _image_identity(path: str) -> dict[str, Any]:
        stat = Path(path).stat()
        return {
            "path": str(Path(path).resolve()), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    @staticmethod
    def _options(kwargs: dict[str, Any]) -> dict[str, Any]:
        from zero import config
        return {
            "timeout": max(1, int(kwargs.get("timeout") or getattr(config, "YARAX_SCAN_TIMEOUT_SECONDS", 300))),
            "max_matches_per_pattern": max(1, int(
                kwargs.get("max_matches_per_pattern")
                or getattr(config, "YARAX_MAX_MATCHES_PER_PATTERN", 1000)
            )),
            "max_result_rows": max(1, int(
                kwargs.get("max_result_rows")
                or getattr(config, "YARAX_MAX_RESULT_ROWS", 100000)
            )),
            "fast_scan": bool(kwargs.get("fast_scan", False)),
            "relaxed_regex": bool(
                kwargs.get("relaxed_regex", getattr(config, "YARAX_RELAXED_REGEX", False))
            ),
        }

    def _cache_paths(
        self, package: dict[str, Any], version: dict[str, Any],
        options: dict[str, Any], runtime_globals: dict[str, Any],
    ) -> tuple[Path, Path]:
        yara_version = importlib.metadata.version("yara-x")
        compile_key = hashlib.sha256(json.dumps({
            "digest": version["content_digest"], "yara_x": yara_version,
            "relaxed_regex": options["relaxed_regex"],
        }, sort_keys=True).encode()).hexdigest()
        compiled = self._store.compile_cache_dir / f"{compile_key}.yarac"
        result_key = hashlib.sha256(json.dumps({
            "package": package["id"], "version_id": version["id"],
            "digest": version["content_digest"],
            "image": self._image_identity(self._image_path or ""),
            "options": options, "globals": runtime_globals, "yara_x": yara_version,
        }, sort_keys=True, default=lambda value: {
            "__bytes__": value.hex()
        } if isinstance(value, bytes) else str(value)).encode()).hexdigest()
        result = self._store.result_cache_dir / f"{package['id']}-{result_key}.json"
        return compiled, result

    def run_plugin(
        self, plugin_name: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> Tuple[List[str], List[Tuple]]:
        if not self.is_plugin_available(plugin_name):
            raise ValueError(f"Plugin not available: {plugin_name}")
        with self._lock:
            if not self._image_loaded or not self._image_path:
                raise ValueError("No memory image loaded")
            if self._plugin_busy:
                raise RuntimeError("A YARA-X scan is already running")
            self._plugin_busy = True
            self._current_plugin = plugin_name
            self._cancel_requested = False
            self._status = "running"
            self._diagnostics = []
            self._cache_hit = False
        try:
            package = self._store.get_package(plugin_name[8:])
            version = self._store.get_version(package["id"], package["active_version_id"])
            options = self._options(kwargs)
            reserved = {
                "use_cache", "timeout", "max_matches_per_pattern", "max_result_rows",
                "fast_scan", "relaxed_regex", "globals",
            }
            runtime_globals = dict(kwargs.get("globals") or {})
            global_specs = version["manifest"].get("globals") or {}
            declared = set(global_specs.keys())
            for name, value in kwargs.items():
                if name not in reserved and name in declared:
                    runtime_globals[name] = value
            for name, value in list(runtime_globals.items()):
                typ = str((global_specs.get(name) or {}).get("type") or "")
                if typ == "int":
                    runtime_globals[name] = int(value)
                elif typ == "float":
                    runtime_globals[name] = float(value)
                elif typ == "bool" and not isinstance(value, bool):
                    normalized = str(value).strip().lower()
                    if normalized not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
                        raise ValueError(f"Global {name!r} must be boolean")
                    runtime_globals[name] = normalized in {"1", "true", "yes", "on"}
                elif typ == "string":
                    runtime_globals[name] = str(value)
                elif typ == "bytes" and isinstance(value, str):
                    runtime_globals[name] = value.encode("utf-8")
            compiled_path, result_path = self._cache_paths(
                package, version, options, runtime_globals,
            )
            use_cache = bool(kwargs.get("use_cache", True))
            if use_cache and result_path.is_file():
                try:
                    cached = json.loads(result_path.read_text(encoding="utf-8"))
                    with self._lock:
                        self._columns = list(cached["columns"])
                        self._rows = [tuple(row) for row in cached["rows"]]
                        self._truncated = bool(cached.get("truncated"))
                        self._status = str(cached.get("status") or "success")
                        self._cache_hit = True
                    if progress_callback:
                        progress_callback("Loaded cached YARA-X results")
                    return list(self._columns), list(self._rows)
                except (OSError, ValueError, KeyError):
                    result_path.unlink(missing_ok=True)

            payload = {
                "package_root": version["path"], "manifest": version["manifest"],
                "compiled_path": str(compiled_path), "image_path": self._image_path,
                "package_name": package["name"], "package_version": version["version"],
                "globals": runtime_globals, **options,
            }
            from zero import config
            method = str(getattr(config, "WORKER_START_METHOD", "spawn") or "spawn")
            try:
                ctx = multiprocessing.get_context(method)
            except ValueError:
                ctx = multiprocessing.get_context("spawn")
            events = ctx.Queue()
            process = ctx.Process(target=_scan_worker, args=(payload, events), daemon=True)
            with self._lock:
                self._process = process
            process.start()
            terminal = None
            # Scanner timeout plus a small IPC/cleanup allowance.
            hard_deadline = time.monotonic() + options["timeout"] + 15
            while terminal is None:
                with self._lock:
                    cancelled = self._cancel_requested
                if cancelled:
                    self._terminate_process(process)
                    with self._lock:
                        self._status = "cancelled"
                    raise ScanCancelledError("YARA-X scan cancelled")
                if time.monotonic() > hard_deadline:
                    self._terminate_process(process)
                    with self._lock:
                        self._status = "timeout"
                    raise ScanTimeoutError(
                        f"YARA-X scan exceeded {options['timeout']} seconds"
                    )
                try:
                    event = events.get(timeout=0.2)
                except queue.Empty:
                    if not process.is_alive():
                        break
                    continue
                if event.get("type") == "progress":
                    if progress_callback:
                        progress_callback(str(event.get("data") or ""))
                else:
                    terminal = event
            process.join(timeout=1)
            if terminal is None:
                with self._lock:
                    self._status = "scan_error"
                raise ScanFailedError(
                    f"YARA-X worker exited without a result (exit code {process.exitcode})"
                )
            if terminal.get("type") == "error":
                with self._lock:
                    self._status = str(terminal.get("status") or "scan_error")
                    self._diagnostics = list(terminal.get("diagnostics") or [])
                if self._status == "timeout":
                    raise ScanTimeoutError(terminal.get("error") or "YARA-X scan timed out")
                if self._status == "compile_error":
                    raise RuleCompileError(terminal.get("error") or "YARA-X compilation failed")
                raise ScanFailedError(terminal.get("error") or "YARA-X scan failed")
            rows = [tuple(row) for row in terminal["rows"]]
            with self._lock:
                self._columns = list(terminal["columns"])
                self._rows = rows
                self._truncated = bool(terminal.get("truncated"))
                self._status = str(terminal.get("status") or "success")
            cache_payload = {
                "columns": self._columns, "rows": terminal["rows"],
                "truncated": self._truncated, "status": self._status,
            }
            tmp = result_path.with_name(result_path.name + f".tmp-{os.getpid()}")
            tmp.write_text(json.dumps(cache_payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, result_path)
            return list(self._columns), list(rows)
        finally:
            with self._lock:
                self._plugin_busy = False
                self._process = None
                if self._status == "running":
                    self._status = "cancelled" if self._cancel_requested else "scan_error"

    @staticmethod
    def _terminate_process(process) -> None:
        if process and process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1)

    def cancel_plugin(self) -> bool:
        with self._lock:
            if not self._plugin_busy:
                return False
            self._cancel_requested = True
            process = self._process
        self._terminate_process(process)
        return True

    def reload_plugins(self) -> int:
        return len(self._store.enabled_packages())

    @staticmethod
    def _sort_key(value: Any):
        if value is None:
            return (2, "")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, value)
        text = str(value)
        try:
            return (0, int(text, 0))
        except (TypeError, ValueError):
            return (1, text.casefold())

    def _view(
        self, filter_text: Optional[str], sort_column: Optional[str], sort_desc: bool
    ) -> tuple[list[str], list[tuple]]:
        with self._lock:
            columns, rows = list(self._columns), list(self._rows)
        if filter_text and filter_text.strip():
            advanced = AdvancedFilter(columns)
            if advanced.set_expression(filter_text):
                rows = advanced.filter_rows(rows)
            else:
                needle = filter_text.casefold()
                rows = [row for row in rows if any(needle in str(v).casefold() for v in row)]
        if sort_column and sort_column in columns:
            index = columns.index(sort_column)
            rows.sort(
                key=lambda row: self._sort_key(row[index] if index < len(row) else None),
                reverse=bool(sort_desc),
            )
        return columns, rows

    def get_results(
        self, filter_text: Optional[str] = None, sort_column: Optional[str] = None,
        sort_desc: bool = False, page: int = 1, page_size: int = 200,
    ) -> Dict[str, Any]:
        columns, rows = self._view(filter_text, sort_column, sort_desc)
        page, page_size = max(1, int(page)), max(1, int(page_size))
        start = (page - 1) * page_size
        with self._lock:
            plugin = self._current_plugin
            status = self._status
            truncated = self._truncated
            diagnostics = list(self._diagnostics)
            cache_hit = self._cache_hit
        return {
            "columns": columns, "rows": [list(row) for row in rows[start:start + page_size]],
            "total": len(rows), "page": page, "page_size": page_size,
            "total_pages": max(1, (len(rows) + page_size - 1) // page_size),
            "current_plugin": plugin, "status": status, "truncated": truncated,
            "diagnostics": diagnostics, "cache_hit": cache_hit,
        }

    def clear_cache(self, plugin_name: Optional[str] = None) -> None:
        package_id = plugin_name[8:] if plugin_name and plugin_name.startswith("package.") else None
        self._store.clear_result_cache(package_id)
        with self._lock:
            if plugin_name is None or plugin_name == self._current_plugin:
                self._columns, self._rows = [], []
                self._current_plugin = None
                self._status = "idle"

    def get_cache_stats(self) -> Dict[str, Any]:
        compiled = list(self._store.compile_cache_dir.glob("*.yarac"))
        results = list(self._store.result_cache_dir.glob("*.json"))
        return {
            "compiled_entries": len(compiled), "result_entries": len(results),
            "compiled_bytes": sum(path.stat().st_size for path in compiled),
            "result_bytes": sum(path.stat().st_size for path in results),
        }

    def export_results(
        self, fmt: str = "csv", filter_text: Optional[str] = None,
        sort_column: Optional[str] = None, sort_desc: bool = False,
    ) -> Optional[str]:
        columns, rows = self._view(filter_text, sort_column, sort_desc)
        if not columns or not rows:
            return None
        plugin = (self._current_plugin or "yarax").replace(".", "_")
        return ResultExporter.auto_export(columns, rows, plugin, fmt=fmt)

    def get_current_result_context(self, max_rows: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._columns or not self._rows:
                return None
            rows = self._rows if max_rows is None else self._rows[:max(1, int(max_rows))]
            return {
                "plugin": self._current_plugin or "unknown", "engine_id": "yarax",
                "columns": list(self._columns), "rows": [list(row) for row in rows],
                "total": len(self._rows), "truncated": self._truncated,
            }

    def get_current_columns(self) -> List[str]:
        with self._lock:
            return list(self._columns)
