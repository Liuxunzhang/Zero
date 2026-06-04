"""Volatility wrapper implementation (currently Volatility3)."""

import logging
import threading
import re
import time
import multiprocessing as mp
import sys
import queue
import subprocess
import json
import selectors
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path

from zero import config
from .result_cache import DiskResultCache

try:
    import volatility3.plugins
    import volatility3.symbols
    from volatility3 import framework
    from volatility3.framework import contexts, automagic, plugins as framework_plugins
    from volatility3.framework.automagic import stacker
    from volatility3.framework.configuration import requirements
    from volatility3.framework import interfaces
    from volatility3.framework import constants as vol_constants
    from volatility3.framework import exceptions as vol_exceptions
    VOLATILITY_AVAILABLE = True
except ImportError:
    VOLATILITY_AVAILABLE = False
    logging.warning("Volatility3 not available")


class VolatilityWrapper:
    """Wrapper class for Volatility3 operations using framework API"""

    _FALLBACK_WINDOWS_PLUGINS = (
        "windows.amcache.Amcache",
        "windows.bigpools.BigPools",
        "windows.callbacks.Callbacks",
        "windows.cmdline.CmdLine",
        "windows.cmdscan.CmdScan",
        "windows.consoles.Consoles",
        "windows.crashinfo.Crashinfo",
        "windows.debugregisters.DebugRegisters",
        "windows.deskscan.DeskScan",
        "windows.desktops.Desktops",
        "windows.devicetree.DeviceTree",
        "windows.dlllist.DllList",
        "windows.driverirp.DriverIrp",
        "windows.drivermodule.DriverModule",
        "windows.driverscan.DriverScan",
        "windows.dumpfiles.DumpFiles",
        "windows.envars.Envars",
        "windows.etwpatch.EtwPatch",
        "windows.filescan.FileScan",
        "windows.getservicesids.GetServiceSIDs",
        "windows.getsids.GetSIDs",
        "windows.handles.Handles",
        "windows.hollowprocesses.HollowProcesses",
        "windows.iat.IAT",
        "windows.info.Info",
        "windows.joblinks.JobLinks",
        "windows.kpcrs.KPCRs",
        "windows.ldrmodules.LdrModules",
        "windows.malfind.Malfind",
        "windows.malware.drivermodule.DriverModule",
        "windows.malware.hollowprocesses.HollowProcesses",
        "windows.malware.ldrmodules.LdrModules",
        "windows.malware.malfind.Malfind",
        "windows.malware.pebmasquerade.PebMasquerade",
        "windows.malware.processghosting.ProcessGhosting",
        "windows.malware.psxview.PsXView",
        "windows.malware.skeleton_key_check.Skeleton_Key_Check",
        "windows.malware.suspicious_threads.SuspiciousThreads",
        "windows.malware.svcdiff.SvcDiff",
        "windows.malware.unhooked_system_calls.UnhookedSystemCalls",
        "windows.mbrscan.MBRScan",
        "windows.memmap.Memmap",
        "windows.modscan.ModScan",
        "windows.modules.Modules",
        "windows.mutantscan.MutantScan",
        "windows.netscan.NetScan",
        "windows.netstat.NetStat",
        "windows.orphan_kernel_threads.Threads",
        "windows.pe_symbols.PESymbols",
        "windows.pedump.PEDump",
        "windows.poolscanner.PoolScanner",
        "windows.privileges.Privs",
        "windows.processghosting.ProcessGhosting",
        "windows.pslist.PsList",
        "windows.psscan.PsScan",
        "windows.pstree.PsTree",
        "windows.psxview.PsXView",
        "windows.registry.amcache.Amcache",
        "windows.registry.certificates.Certificates",
        "windows.registry.getcellroutine.GetCellRoutine",
        "windows.registry.hivelist.HiveList",
        "windows.registry.hivescan.HiveScan",
        "windows.registry.printkey.PrintKey",
        "windows.registry.scheduled_tasks.ScheduledTasks",
        "windows.registry.userassist.UserAssist",
        "windows.scheduled_tasks.ScheduledTasks",
        "windows.sessions.Sessions",
        "windows.shimcachemem.ShimcacheMem",
        "windows.skeleton_key_check.Skeleton_Key_Check",
        "windows.ssdt.SSDT",
        "windows.statistics.Statistics",
        "windows.strings.Strings",
        "windows.suspended_threads.SuspendedThreads",
        "windows.suspicious_threads.SuspiciousThreads",
        "windows.svcdiff.SvcDiff",
        "windows.svclist.SvcList",
        "windows.svcscan.SvcScan",
        "windows.symlinkscan.SymlinkScan",
        "windows.thrdscan.ThrdScan",
        "windows.threads.Threads",
        "windows.timers.Timers",
        "windows.truecrypt.Passphrase",
        "windows.unhooked_system_calls.unhooked_system_calls",
        "windows.unloadedmodules.UnloadedModules",
        "windows.vadinfo.VadInfo",
        "windows.vadregexscan.VadRegExScan",
        "windows.vadwalk.VadWalk",
        "windows.verinfo.VerInfo",
        "windows.virtmap.VirtMap",
        "windows.windows.Windows",
        "windows.windowstations.WindowStations",
    )

    def __init__(self, image_path: Optional[str] = None):
        self.image_path = image_path
        self.plugin_list = []
        self._display_to_full_plugin_name: Dict[str, str] = {}
        self._current_plugin_family = "linux"
        self._cache = {}  # Cache for plugin results: {plugin_name: (columns, rows)}
        self._cache_stats: Dict[str, int] = {
            "memory_hits": 0,
            "disk_hits": 0,
            "misses": 0,
            "disk_writes": 0,
            "clear_operations": 0,
        }
        self._state_lock = threading.Lock()
        self._plugin_running = False
        self._cancel_requested = threading.Event()
        self._worker_process: Optional[Any] = None
        self.plugin_timeout_seconds = int(getattr(config, "PLUGIN_TIMEOUT_SECONDS", 600))
        self.stall_timeout_seconds = int(getattr(config, "PLUGIN_STALL_TIMEOUT_SECONDS", 120))
        self.enable_disk_cache = bool(getattr(config, "ENABLE_DISK_CACHE", True))
        self.disk_cache_format = str(getattr(config, "DISK_CACHE_FORMAT", "csv")).lower()
        self.progress_log_throttle_seconds = float(
            getattr(config, "PROGRESS_LOG_THROTTLE_SECONDS", 0.5)
        )
        self.terminate_grace_seconds = float(getattr(config, "TERMINATE_GRACE_SECONDS", 2.0))
        self.hard_interrupt_mode = bool(getattr(config, "HARD_INTERRUPT_MODE", True))
        self.worker_start_method = str(getattr(config, "WORKER_START_METHOD", "fork")).lower()
        self.results_root = self._resolve_results_root()
        self._disk_cache = DiskResultCache(
            results_root=self.results_root,
            enabled=self.enable_disk_cache,
            fmt=self.disk_cache_format,
        )
        self.symbol_dirs = self._resolve_symbol_dirs()
        self._init_volatility()

    def _get_mp_context(self) -> mp.context.BaseContext:
        """Get multiprocessing context for plugin worker process."""
        methods = mp.get_all_start_methods()
        preferred = self.worker_start_method if self.worker_start_method in methods else None

        # macOS Objective-C runtimes are not fork-safe once initialized.
        # Using fork from a Textual app can crash immediately with:
        # "crashed on child side of fork pre-exec".
        if sys.platform == "darwin" and preferred == "fork" and "spawn" in methods:
            logging.warning("macOS detected; overriding WORKER_START_METHOD=fork to spawn")
            preferred = "spawn"

        if preferred:
            return mp.get_context(preferred)

        if sys.platform == "darwin" and "spawn" in methods:
            return mp.get_context("spawn")

        # Linux typically supports fork; fallback to default.
        return mp.get_context()

    def _terminate_worker_process(self, force: bool = False, wait: bool = True) -> bool:
        """Terminate currently running worker process."""
        process = self._worker_process
        if not process:
            return False

        is_alive = False
        if hasattr(process, "is_alive"):
            is_alive = bool(process.is_alive())
        elif hasattr(process, "poll"):
            is_alive = process.poll() is None

        if not is_alive:
            return False

        try:
            if force:
                process.kill()
            else:
                process.terminate()
                if wait:
                    if hasattr(process, "join"):
                        process.join(timeout=self.terminate_grace_seconds)
                        still_alive = process.is_alive()
                    else:
                        try:
                            process.wait(timeout=self.terminate_grace_seconds)
                            still_alive = False
                        except subprocess.TimeoutExpired:
                            still_alive = True

                    if still_alive:
                        process.kill()
            if wait:
                if hasattr(process, "join"):
                    process.join(timeout=1.0)
                else:
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        logging.warning(
                            "Worker process did not exit within 1 s after kill; "
                            "it may remain as a zombie."
                        )
            return True
        except Exception as e:
            logging.error(f"Failed to terminate worker process: {e}", exc_info=True)
            return False

    @staticmethod
    def _format_unsatisfied_exception(exc: "vol_exceptions.UnsatisfiedException") -> str:
        """Format Volatility unsatisfied requirements into a user-facing message."""
        parts = []
        for key, requirement in getattr(exc, "unsatisfied", {}).items():
            req_type = type(requirement).__name__
            description = getattr(requirement, "description", "") or ""
            optional = getattr(requirement, "optional", None)
            suffix = f": {description}" if description else ""
            optional_text = "" if optional is None else f", optional={optional}"
            parts.append(f"{key} ({req_type}{optional_text}){suffix}")
        return "; ".join(parts) or "unknown requirement"

    @staticmethod
    def _refresh_symbol_cache(symbol_dirs: List[str]) -> Dict[str, int]:
        """Refresh Volatility symbol identifier cache and return banner counts."""
        import os as _os

        import volatility3.symbols as _symbols
        from volatility3.framework import constants as _constants
        from volatility3.framework.automagic import symbol_cache as _symbol_cache

        _symbols.__path__ = [
            str(Path(p).resolve()) for p in symbol_dirs if Path(p).exists()
        ] + _constants.SYMBOL_BASEPATHS

        _os.makedirs(_constants.CACHE_PATH, exist_ok=True)
        cache_file = _os.path.join(_constants.CACHE_PATH, _constants.IDENTIFIERS_FILENAME)
        cache = _symbol_cache.SqliteCache(cache_file)
        cache.update()
        linux_count = len([
            key for key in cache.get_identifier_dictionary(operating_system="linux")
            if key
        ])
        windows_count = len([
            key for key in cache.get_identifier_dictionary(operating_system="windows")
            if key
        ])
        return {"linux": linux_count, "windows": windows_count}

    @staticmethod
    def _api_worker_entry(
        image_path: str,
        symbol_dirs: List[str],
        plugin_name: str,
        out_queue: "mp.Queue",
        plugin_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Run a Volatility plugin in a child process and report via queue."""
        import io
        import os as _os

        plugin_kwargs = plugin_kwargs or {}

        try:
            import volatility3.plugins
            import volatility3.symbols
            from volatility3 import framework
            from volatility3.framework import contexts, automagic, plugins as framework_plugins
            from volatility3.framework.automagic import stacker
            from volatility3.framework.configuration import requirements
            from volatility3.framework import interfaces
            from volatility3.framework import constants as vol_constants

            framework.require_interface_version(2, 0, 0)
            failures = framework.import_files(volatility3.plugins, True)
            if failures:
                out_queue.put(("log", f"Plugin import failures: {len(failures)}"))

            volatility3.symbols.__path__ = [
                str(Path(p).resolve()) for p in symbol_dirs
            ] + vol_constants.SYMBOL_BASEPATHS
            symbol_counts = VolatilityWrapper._refresh_symbol_cache(symbol_dirs)
            out_queue.put((
                "log",
                "Symbol cache refreshed: "
                f"linux={symbol_counts.get('linux', 0)}, "
                f"windows={symbol_counts.get('windows', 0)}, "
                f"dirs={symbol_dirs or '[]'}",
            ))

            ctx = contexts.Context()
            plugin_map = framework.list_plugins()
            if plugin_name not in plugin_map:
                out_queue.put(("error", f"Unknown plugin: {plugin_name}"))
                return

            plugin_class = plugin_map[plugin_name]
            base_config_path = "plugins"
            ctx.config["automagic.LayerStacker.single_location"] = (
                requirements.URIRequirement.location_from_file(image_path)
            )

            # Apply user-supplied kwargs to ctx.config.
            from zero.core.plugin_worker import _apply_plugin_kwargs
            _apply_plugin_kwargs(ctx, plugin_class, base_config_path, plugin_kwargs)

            automagics = automagic.available(ctx)
            automagics = automagic.choose_automagic(automagics, plugin_class)
            if ctx.config.get("automagic.LayerStacker.stackers", None) is None:
                ctx.config["automagic.LayerStacker.stackers"] = stacker.choose_os_stackers(
                    plugin_class
                )

            def framework_progress(progress: float, description: Optional[str] = None):
                msg = f"Progress: {float(progress):.2f} {description or ''}".strip()
                out_queue.put(("progress", msg))

            # Provide a concrete FileHandlerInterface for dump-type plugins.
            from zero.core.plugin_worker import _make_file_handler, _default_dump_dir
            dump_dir = str(plugin_kwargs.get("dump_dir") or _default_dump_dir())
            file_handler_class = _make_file_handler(dump_dir)

            constructed = framework_plugins.construct_plugin(
                ctx,
                automagics,
                plugin_class,
                base_config_path,
                framework_progress,
                file_handler_class,
            )

            grid = constructed.run()
            columns = [col.name for col in grid.columns]
            rows: List[Tuple[str, ...]] = []

            def visitor(node, accumulator):
                values = grid.values(node)
                row: Tuple[str, ...] = tuple(
                    (
                        value.decode("utf-8", errors="replace")
                        if isinstance(value, bytes)
                        else ("" if value is None else str(value))
                    )
                    for value in values
                )
                rows.append(row)
                return accumulator

            try:
                grid.populate(visitor, None)
            except vol_exceptions.InvalidAddressException as e:
                message = (
                    f"{type(e).__name__}: {e}. "
                    "已跳过后续不可读对象并返回部分结果。"
                )
                if rows:
                    out_queue.put(("progress", message))
                else:
                    out_queue.put(("error", {
                        "message": message,
                        "traceback": "",
                    }))
                    return
            out_queue.put(("result", (columns, rows)))
        except vol_exceptions.UnsatisfiedException as e:
            detail = VolatilityWrapper._format_unsatisfied_exception(e)
            hint = (
                "Volatility 未满足插件运行条件。Linux 插件通常表示镜像内核 banner "
                "没有匹配到本地符号表，或符号表与镜像内核版本不一致。"
            )
            out_queue.put(("error", {
                "message": f"Unsatisfied requirements: {detail}. {hint}",
                "traceback": "",
            }))
        except Exception as e:
            import traceback
            out_queue.put(("error", {"message": str(e), "traceback": traceback.format_exc()}))

    def _set_running_state(self, running: bool) -> None:
        with self._state_lock:
            self._plugin_running = running

    def _increment_cache_stat(self, key: str, value: int = 1) -> None:
        with self._state_lock:
            self._cache_stats[key] = int(self._cache_stats.get(key, 0)) + int(value)

    def _is_running(self) -> bool:
        with self._state_lock:
            return self._plugin_running

    def _diagnose_error(self, stderr_text: str, plugin_name: str) -> str:
        """Return a concise diagnosis hint for common Volatility errors."""
        text = stderr_text.lower()

        # This fires when plugin_worker already extracted the missing requirements cleanly.
        if "必填参数" in text or "无法直接运行" in text:
            return (
                "此插件需要额外参数（如 YARA 规则、目标 PID 等），"
                "当前版本暂不支持交互式传参，请选择其他插件或手动使用 vol3 命令行传参。"
            )
        if "unsatisfiedexception" in text or "unsatisfied" in text:
            if "symbol table" in text or "symbol" in text:
                return (
                    "可能是符号表不匹配或缺失。请确认 symbols/ 下存在对应镜像内核 banner 的 "
                    "json/json.xz 符号表；导入符号表后重新运行插件即可，无需重启服务。"
                )
            return (
                "Volatility 未能自动构建插件所需的 layer 或 symbol table。"
                "请先确认镜像类型与插件平台一致，并检查符号表是否匹配。"
            )
        if "symbol table" in text:
            return "可能是符号表不匹配或缺失，请确认 json 符号与镜像内核版本一致。"
        if "page fault" in text or "invalidaddressexception" in text:
            return (
                "插件读取到不可映射的内存地址，常见于镜像不完整、进程结构已释放、"
                "内核结构字段不完全匹配或该插件对当前内核版本兼容性不足。"
                "可先用 pslist/psaux 验证基础进程视图，再对 lsof 使用 pid 参数缩小范围。"
            )
        if "layer" in text and "invalid" in text:
            return "可能镜像格式不支持或镜像损坏，请先用基础插件验证镜像可读性。"
        if "permission denied" in text:
            return "权限不足，请检查镜像文件和目录读权限。"
        if "no such file" in text:
            return "路径不存在，请确认镜像路径或依赖文件路径正确。"
        if "killed" in text or "terminated" in text:
            return "插件被中断或系统回收，建议重试并观察资源占用。"

        log_file = str(getattr(config, "LOG_FILE", "logs/zero.log"))
        return f"插件 {plugin_name} 执行失败，请查看 {log_file} 获取完整错误。"

    def _should_use_subprocess_worker(self) -> bool:
        """Use a standalone Python subprocess on macOS for reliability."""
        return sys.platform == "darwin"

    def _resolve_results_root(self) -> Path:
        """Resolve and create persistent result cache root folder.

        Results are stored under saved_results/vol3/ for predictable cache layout.
        """
        configured_dir = str(getattr(config, "RESULTS_CACHE_DIR", "saved_results"))
        root = Path(configured_dir).expanduser()
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[2] / root
        # Engine-specific subdirectory for cache isolation
        root = root / "vol3"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def terminate_running_plugin(self, force: bool = False, wait: bool = True) -> bool:
        """Request cancellation of current running plugin.

        Note: API mode uses cooperative cancellation. If a plugin does not invoke
        progress callbacks internally, cancellation may be delayed until it returns.
        """
        if not self._is_running():
            return False
        if self.hard_interrupt_mode:
            return self._terminate_worker_process(force=force, wait=wait)
        self._cancel_requested.set()
        return True

    def _resolve_symbol_dirs(self) -> List[str]:
        """Resolve symbol directories from project defaults only."""
        dirs: List[str] = []

        configured = str(getattr(config, "SYMBOL_TABLE_RELATIVE_PATH", "json")).strip() or "json"
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = Path(__file__).resolve().parents[2] / configured_path

        if configured_path.exists() and configured_path.is_dir():
            dirs.append(str(configured_path))
            return dirs

        # Fallback: <repo>/json
        project_json_dir = Path(__file__).resolve().parents[2] / "json"
        if project_json_dir.exists() and project_json_dir.is_dir():
            dirs.append(str(project_json_dir))

        return dirs

    def _resolve_custom_plugin_dirs(self) -> List[str]:
        """Resolve custom plugin directories from config."""
        dirs: List[str] = []
        configured = getattr(config, "VOLATILITY_PLUGINS_PATH", None)
        if not configured:
            return dirs

        configured_path = Path(str(configured)).expanduser()
        if not configured_path.is_absolute():
            configured_path = Path(__file__).resolve().parents[2] / configured_path

        if configured_path.exists() and configured_path.is_dir():
            dirs.append(str(configured_path.resolve()))
        return dirs

    def _init_volatility(self):
        """Initialize Volatility3 framework to get plugin list"""
        if not VOLATILITY_AVAILABLE:
            logging.error("Volatility3 is not installed")
            return

        try:
            framework.require_interface_version(2, 0, 0)

            # Load built-in plugins FIRST (safe).
            framework.import_files(volatility3.plugins, True)
            # Make project symbol directory discoverable by framework API mode.
            volatility3.symbols.__path__ = [
                str(Path(p).resolve()) for p in self.symbol_dirs
            ] + vol_constants.SYMBOL_BASEPATHS
            self.plugin_list = list(framework.list_plugins())
            logging.info(f"Loaded {len(self.plugin_list)} built-in plugins")
        except Exception as e:
            logging.error(f"Failed to initialize Volatility3: {e}")
            return

        # Then try custom plugin directories separately (isolated errors).
        self._load_custom_plugins()

    def _load_custom_plugins(self):
        """Try to load custom Volatility 3 plugins from plugins/ dir."""
        custom_plugin_dirs = self._resolve_custom_plugin_dirs()
        if not custom_plugin_dirs:
            return

        added = 0
        for d in custom_plugin_dirs:
            base = Path(d).resolve()

            candidates: List[Path] = []
            if base.is_dir():
                has_py_files = any(base.glob("*.py"))
                if has_py_files:
                    candidates.append(base)
                for subdir in sorted(base.iterdir()):
                    if subdir.is_dir():
                        candidates.append(subdir)
            else:
                candidates.append(base)

            for candidate in candidates:
                candidate = candidate.resolve()
                if str(candidate) not in volatility3.plugins.__path__:
                    volatility3.plugins.__path__.append(str(candidate))
                    logging.info(f"Added custom plugin directory: {candidate}")
                    added += 1

        if added == 0:
            return

        try:
            framework.import_files(volatility3.plugins, True)
            new_list = list(framework.list_plugins())
            new_count = len(new_list) - len(self.plugin_list)
            self.plugin_list = new_list
            if new_count > 0:
                logging.info(f"Loaded {new_count} custom plugins")
        except Exception as e:
            logging.warning(f"Custom plugin loading failed (built-in plugins unaffected): {e}")

    def reload_plugins(self) -> int:
        """Re-scan plugin directories and reload the plugin list.

        Returns the number of plugins discovered.
        """
        if not VOLATILITY_AVAILABLE:
            return 0
        try:
            # Reload built-in plugins.
            framework.import_files(volatility3.plugins, True)
            self.plugin_list = list(framework.list_plugins())
            logging.info(f"Reloaded built-in plugins: {len(self.plugin_list)} total")
        except Exception as e:
            logging.error(f"Failed to reload plugins: {e}")

        # Then try custom plugins separately.
        self._load_custom_plugins()
        return len(self.plugin_list)

    def load_image(self, image_path: str) -> bool:
        """Load a memory image"""
        try:
            img_path = Path(image_path)
            if not img_path.exists():
                logging.error(f"Image file does not exist: {image_path}")
                return False

            self.image_path = str(img_path.absolute())
            # Clear cache when loading new image
            self._cache.clear()
            self._increment_cache_stat("clear_operations")
            logging.info(f"Successfully loaded image: {self.image_path}")
            return True
        except Exception as e:
            logging.error(f"Failed to load image: {e}", exc_info=True)
            return False

    def get_available_plugins(self) -> List[Dict[str, str]]:
        """Get list of available Volatility3 plugins"""
        result = []
        for plugin_name in self.plugin_list:
            # plugin_name is a string like "linux.pslist.PsList"
            result.append({
                "name": self._to_display_plugin_name(plugin_name),
                "description": "Volatility3 plugin"
            })
        return result

    def _to_display_plugin_name(self, plugin_name: str) -> str:
        """Convert full plugin name to UI display name."""
        if plugin_name.startswith("linux."):
            return plugin_name[len("linux."):]
        if plugin_name.startswith("windows."):
            return plugin_name[len("windows."):]
        return plugin_name

    def _resolve_plugin_name(self, plugin_name: str) -> str:
        """Resolve UI/plugin input name to a full framework plugin name."""
        if plugin_name in self.plugin_list:
            return plugin_name

        mapped = self._display_to_full_plugin_name.get(plugin_name)
        if mapped and mapped in self.plugin_list:
            return mapped

        prefixed = f"{self._current_plugin_family}.{plugin_name}"
        if prefixed in self.plugin_list:
            return prefixed

        linux_prefixed = f"linux.{plugin_name}"
        if linux_prefixed in self.plugin_list:
            return linux_prefixed

        windows_prefixed = f"windows.{plugin_name}"
        if windows_prefixed in self.plugin_list:
            return windows_prefixed

        return plugin_name

    def resolve_plugin_name(self, plugin_name: str) -> str:
        """Public resolver for UI-side plugin validation."""
        return self._resolve_plugin_name(plugin_name)

    def is_plugin_available(self, plugin_name: str) -> bool:
        """Return True if plugin can be resolved to an installed framework plugin."""
        resolved = self._resolve_plugin_name(plugin_name)
        return resolved in self.plugin_list

    def run_plugin(self, plugin_name: str, progress_callback=None, use_cache: bool = True, **kwargs) -> Tuple[List[str], List[Tuple]]:
        """Run a Volatility3 plugin using framework API

        Args:
            plugin_name: Name of the plugin to run
            progress_callback: Optional callback for progress updates
            use_cache: If True, return cached results if available

        Returns:
            Tuple of (columns, rows)
        """
        if not self.image_path:
            raise ValueError("No memory image loaded")

        resolved_plugin_name = self._resolve_plugin_name(plugin_name)
        display_plugin_name = self._to_display_plugin_name(resolved_plugin_name)

        # Check cache first
        if use_cache and resolved_plugin_name in self._cache:
            logging.info(f"Returning cached results for {resolved_plugin_name}")
            self._increment_cache_stat("memory_hits")
            if progress_callback:
                progress_callback("Using cached results")
            return self._cache[resolved_plugin_name]

        # Check persistent disk cache
        if use_cache:
            disk_cached = self._disk_cache.load(self.image_path, resolved_plugin_name)
            if disk_cached is not None:
                self._cache[resolved_plugin_name] = disk_cached
                logging.info(f"Loaded persistent cached results for {resolved_plugin_name}")
                self._increment_cache_stat("disk_hits")
                if progress_callback:
                    progress_callback(f"Loaded saved results: {display_plugin_name}")
                return disk_cached

        self._increment_cache_stat("misses")

        logging.info(f"Running plugin via API: {resolved_plugin_name}")
        if progress_callback:
            progress_callback(f"Starting: {display_plugin_name}")

        if not VOLATILITY_AVAILABLE:
            raise ValueError("Volatility3 is not installed")

        self._cancel_requested.clear()
        self._set_running_state(True)
        # Symbol tables may be imported while the web service is already running.
        # Re-resolve per plugin run so newly created symbols/ files are visible.
        self.symbol_dirs = self._resolve_symbol_dirs()

        if not self.hard_interrupt_mode:
            raise ValueError("当前版本仅支持硬中断模式，请在 config 中开启 HARD_INTERRUPT_MODE")

        try:
            if self._should_use_subprocess_worker():
                columns, data_rows = self._run_plugin_via_subprocess(
                    resolved_plugin_name,
                    progress_callback,
                    plugin_kwargs=kwargs,
                )
            else:
                columns, data_rows = self._run_plugin_via_multiprocessing(
                    resolved_plugin_name,
                    progress_callback,
                    plugin_kwargs=kwargs,
                )

            self._cache[resolved_plugin_name] = (columns, data_rows)
            if self._disk_cache.save(self.image_path, resolved_plugin_name, columns, data_rows):
                self._increment_cache_stat("disk_writes")
            return columns, data_rows
        finally:
            self._set_running_state(False)
            self._worker_process = None

    def _run_plugin_via_multiprocessing(
        self,
        resolved_plugin_name: str,
        progress_callback=None,
        plugin_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[str], List[Tuple]]:
        ctx = self._get_mp_context()
        out_queue = ctx.Queue()
        process = ctx.Process(
            target=VolatilityWrapper._api_worker_entry,
            args=(self.image_path, self.symbol_dirs, resolved_plugin_name, out_queue, plugin_kwargs or {}),
            daemon=True,
        )
        self._worker_process = process
        process.start()

        last_progress = None
        last_progress_emit_at = 0.0
        started_at = time.monotonic()
        last_activity_at = started_at
        result_payload: Optional[Tuple[List[str], List[Tuple]]] = None
        error_text: Optional[str] = None

        while process.is_alive():
            now = time.monotonic()
            if now - started_at > self.plugin_timeout_seconds:
                self._terminate_worker_process(force=True)
                raise ValueError(f"插件执行超时（>{self.plugin_timeout_seconds}s），已自动中断。")

            if now - last_activity_at > self.stall_timeout_seconds:
                self._terminate_worker_process(force=True)
                raise ValueError(f"插件疑似卡死（{self.stall_timeout_seconds}s 无进度），已自动中断。")

            try:
                event_type, payload = out_queue.get(timeout=0.1)
                last_activity_at = time.monotonic()

                if event_type == "progress":
                    progress_msg = str(payload)
                    now = time.monotonic()
                    should_emit = (
                        progress_callback is not None
                        and progress_msg != last_progress
                        and (
                            (now - last_progress_emit_at) >= self.progress_log_throttle_seconds
                            or "100" in progress_msg
                            or "starting" in progress_msg.lower()
                            or "loaded saved" in progress_msg.lower()
                        )
                    )
                    if should_emit:
                        progress_callback(progress_msg)
                        last_progress = progress_msg
                        last_progress_emit_at = now
                elif event_type == "result":
                    result_payload = payload
                elif event_type == "error":
                    if isinstance(payload, dict):
                        error_text = str(payload.get("message", ""))
                        if "traceback" in payload:
                            logging.error(f"Plugin Error Traceback:\n{payload['traceback']}")
                    else:
                        error_text = str(payload)
                elif event_type == "log":
                    logging.info(str(payload))
            except queue.Empty:
                pass

        while True:
            try:
                event_type, payload = out_queue.get_nowait()
                if event_type == "result":
                    result_payload = payload
                elif event_type == "error":
                    if isinstance(payload, dict):
                        error_text = str(payload.get("message", ""))
                        if "traceback" in payload:
                            logging.error(f"Plugin Error Traceback:\n{payload['traceback']}")
                    else:
                        error_text = str(payload)
                elif event_type == "progress" and progress_callback:
                    progress_msg = str(payload)
                    if progress_msg != last_progress:
                        progress_callback(progress_msg)
                        last_progress = progress_msg
                elif event_type == "log":
                    logging.info(str(payload))
            except queue.Empty:
                break

        if result_payload is not None:
            return result_payload

        if error_text:
            hint = self._diagnose_error(error_text, resolved_plugin_name)
            raise ValueError(f"Plugin execution failed: {error_text[:500]} | 建议: {hint}")

        if self._cancel_requested.is_set():
            raise RuntimeError("Plugin execution cancelled")

        raise ValueError("插件执行失败，未返回结果")

    def _run_plugin_via_subprocess(
        self,
        resolved_plugin_name: str,
        progress_callback=None,
        plugin_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[str], List[Tuple]]:
        command = [
            sys.executable,
            "-m",
            "zero.core.plugin_worker",
            "--image",
            self.image_path,
            "--plugin",
            resolved_plugin_name,
            "--symbols",
            json.dumps(self.symbol_dirs, ensure_ascii=False),
            "--kwargs",
            json.dumps(plugin_kwargs or {}, ensure_ascii=False),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        self._worker_process = process

        assert process.stdout is not None
        assert process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)

        last_progress = None
        last_progress_emit_at = 0.0
        started_at = time.monotonic()
        last_activity_at = started_at
        result_payload: Optional[Tuple[List[str], List[Tuple]]] = None
        error_text: Optional[str] = None

        while True:
            now = time.monotonic()
            if now - started_at > self.plugin_timeout_seconds:
                self._terminate_worker_process(force=True)
                raise ValueError(f"插件执行超时（>{self.plugin_timeout_seconds}s），已自动中断。")

            if now - last_activity_at > self.stall_timeout_seconds:
                self._terminate_worker_process(force=True)
                raise ValueError(f"插件疑似卡死（{self.stall_timeout_seconds}s 无进度），已自动中断。")

            ready = selector.select(timeout=0.1)
            if ready:
                line = process.stdout.readline()
                if line:
                    last_activity_at = time.monotonic()
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_type = message.get("type")
                    payload = message.get("payload")

                    if event_type == "progress":
                        progress_msg = str(payload)
                        now = time.monotonic()
                        should_emit = (
                            progress_callback is not None
                            and progress_msg != last_progress
                            and (
                                (now - last_progress_emit_at) >= self.progress_log_throttle_seconds
                                or "100" in progress_msg
                                or "starting" in progress_msg.lower()
                                or "loaded saved" in progress_msg.lower()
                            )
                        )
                        if should_emit:
                            progress_callback(progress_msg)
                            last_progress = progress_msg
                            last_progress_emit_at = now
                    elif event_type == "result":
                        result_payload = (
                            list(payload.get("columns", [])),
                            [tuple(row) for row in payload.get("rows", [])],
                        )
                    elif event_type == "error":
                        if isinstance(payload, dict):
                            error_text = str(payload.get("message", ""))
                            if "traceback" in payload:
                                logging.error(f"Plugin Error Traceback:\n{payload['traceback']}")
                        else:
                            error_text = str(payload)

            if process.poll() is not None:
                break

        stderr_text = process.stderr.read().strip()
        selector.close()
        if stderr_text:
            logging.error(stderr_text)
            if not error_text:
                error_text = stderr_text

        if result_payload is not None:
            return result_payload

        if error_text:
            hint = self._diagnose_error(error_text, resolved_plugin_name)
            raise ValueError(f"Plugin execution failed: {error_text[:500]} | 建议: {hint}")

        if self._cancel_requested.is_set():
            raise RuntimeError("Plugin execution cancelled")

        raise ValueError("插件执行失败，未返回结果")

    def clear_cache(self, plugin_name: Optional[str] = None):
        """Clear cached results

        Args:
            plugin_name: If specified, clear only this plugin's cache.
                        If None, clear all cache.
        """
        if plugin_name:
            resolved_plugin_name = self._resolve_plugin_name(plugin_name)
            self._cache.pop(resolved_plugin_name, None)
            self._increment_cache_stat("clear_operations")
            logging.info(f"Cleared cache for {resolved_plugin_name}")
        else:
            self._cache.clear()
            self._increment_cache_stat("clear_operations")
            logging.info("Cleared all cache")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache hit/miss counters and current cache footprint."""
        with self._state_lock:
            stats = dict(self._cache_stats)
        return {
            **stats,
            "memory_entries": len(self._cache),
            "disk_cache_enabled": self.enable_disk_cache,
            "disk_cache_format": self.disk_cache_format,
            "results_cache_dir": str(self.results_root),
        }

    @staticmethod
    def _load_category_config() -> Optional[Dict]:
        """Load plugin_categories.json from project root."""
        config_path = Path(__file__).resolve().parents[2] / "plugin_categories.json"
        if not config_path.exists():
            return None
        try:
            with config_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Failed to load plugin_categories.json: {e}")
            return None

    def get_plugin_categories(self, plugin_family: str = "linux") -> Dict[str, List[str]]:
        """Categorize plugins by OS family using JSON config (with hardcoded fallback)."""
        self._display_to_full_plugin_name.clear()
        family = (plugin_family or "linux").lower()
        if family not in {"linux", "windows"}:
            family = "linux"
        self._current_plugin_family = family

        source_plugins = [p for p in self.plugin_list if p.startswith(f"{family}.")]
        if family == "windows":
            source_plugins = sorted(set(source_plugins + list(self._FALLBACK_WINDOWS_PLUGINS)))

        # Build display-name mapping.
        for plugin_name in source_plugins:
            simple_name = self._to_display_plugin_name(plugin_name)
            self._display_to_full_plugin_name[simple_name] = plugin_name

        # Always use hardcoded categorization.
        result = self._categorize_hardcoded(family, source_plugins)

        # Then merge any extra categories from JSON config (additive only).
        cfg = self._load_category_config()
        if cfg:
            extra = cfg.get("extra_categories", {}).get(family, {})
            if extra:
                result = self._merge_extra_categories(result, extra, source_plugins)

        return result

    def _merge_extra_categories(
        self, base: Dict[str, List[str]], extra_cfg: Dict[str, List[str]],
        source_plugins: List[str],
    ) -> Dict[str, List[str]]:
        """Merge user-defined extra categories into the base result.

        Extra categories have the HIGHEST priority — they can claim plugins
        from any existing category (including specific ones, not just '其他').
        Claimed plugins are removed from their original category.
        """
        for cat_name, keywords in extra_cfg.items():
            matched = []
            for plugin_name in source_plugins:
                simple_name = self._to_display_plugin_name(plugin_name)
                if any(kw.lower() in plugin_name.lower() for kw in keywords):
                    matched.append(simple_name)

            if matched:
                # Remove these plugins from their current categories.
                matched_set = set(matched)
                for existing_cat in list(base.keys()):
                    base[existing_cat] = [
                        p for p in base[existing_cat] if p not in matched_set
                    ]

                # Add the new category; clean up empty categories.
                base[cat_name] = sorted(matched)

        # Remove categories that became empty after stealing.
        return {cat: plugins for cat, plugins in base.items() if plugins}

    def _categorize_from_config(
        self, cfg: Dict, family: str, source_plugins: List[str]
    ) -> Dict[str, List[str]]:
        """Categorize plugins using the JSON config file."""
        family_cfg: Dict[str, List[str]] = cfg.get(family, {})
        exclude_cfg: Dict[str, List[str]] = (
            cfg.get("exclude_keywords", {}).get(family, {})
        )

        # Ordered categories dict preserving config order + 未分类.
        categories: Dict[str, List[str]] = {cat: [] for cat in family_cfg}
        categories["未分类"] = []

        for plugin_name in source_plugins:
            simple_name = self._to_display_plugin_name(plugin_name)
            name_lower = plugin_name.lower()

            matched = False
            for cat_name, keywords in family_cfg.items():
                # Check exclude keywords for this category.
                excludes = exclude_cfg.get(cat_name, [])
                if excludes and any(ex in name_lower for ex in excludes):
                    continue

                if any(kw.lower() in name_lower for kw in keywords):
                    categories[cat_name].append(simple_name)
                    matched = True
                    break

            if not matched:
                categories["未分类"].append(simple_name)

        # Sort each category and remove empty ones.
        return {
            cat: sorted(plugins)
            for cat, plugins in categories.items()
            if plugins
        }

    def _categorize_hardcoded(
        self, family: str, source_plugins: List[str]
    ) -> Dict[str, List[str]]:
        """Original hardcoded categorization as fallback."""
        if family == "windows":
            categories = {
                "进程相关": [],
                "注册表相关": [],
                "网络相关": [],
                "驱动/内核": [],
                "内存与代码": [],
                "恶意行为检测": [],
                "系统信息": [],
                "其他": [],
            }
        else:
            categories = {
                "进程相关": [],
                "文件/模块": [],
                "网络相关": [],
                "内存/恶意代码": [],
                "安全检查/Rootkit": [],
                "系统信息/调试": [],
                "追踪/调试": [],
                "Malware专项": [],
                "其他": [],
            }

        for plugin_name in source_plugins:
            simple_name = self._to_display_plugin_name(plugin_name)
            name_lower = plugin_name.lower()

            if family == "windows":
                if any(x in name_lower for x in [
                    "pslist", "psscan", "pstree", "cmdline", "envars",
                    "handles", "threads", "thrdscan", "sessions", "joblinks"
                ]):
                    categories["进程相关"].append(simple_name)
                elif "registry." in name_lower or any(x in name_lower for x in [
                    "hivelist", "hivescan", "printkey", "userassist", "amcache", "certificates"
                ]):
                    categories["注册表相关"].append(simple_name)
                elif any(x in name_lower for x in ["netscan", "netstat", "socket", "conn"]):
                    categories["网络相关"].append(simple_name)
                elif any(x in name_lower for x in [
                    "driverscan", "drivermodule", "driverirp", "modules", "modscan",
                    "callbacks", "ssdt", "kpcr", "timers", "devicetree", "unloadedmodules"
                ]):
                    categories["驱动/内核"].append(simple_name)
                elif any(x in name_lower for x in [
                    "malfind", "vad", "memmap", "virtmap", "poolscanner", "strings",
                    "dlllist", "ldrmodules", "iat", "pedump", "pe_symbols", "dumpfiles"
                ]):
                    categories["内存与代码"].append(simple_name)
                elif "malware" in name_lower or any(x in name_lower for x in [
                    "skeleton_key", "svcdiff", "suspicious_threads", "unhooked_system_calls",
                    "processghosting", "psxview", "etwpatch", "hollowprocesses", "mbrscan"
                ]):
                    categories["恶意行为检测"].append(simple_name)
                elif any(x in name_lower for x in [
                    "info", "crashinfo", "statistics", "windows.", "windowstations", "desktops", "deskscan"
                ]):
                    categories["系统信息"].append(simple_name)
                else:
                    categories["其他"].append(simple_name)
                continue

            if any(x in name_lower for x in ["pslist", "psscan", "pstree", "psaux", "envars", "kthreads"]):
                categories["进程相关"].append(simple_name)
            elif any(x in name_lower for x in ["lsof", "lsmod", "modxview", "hidden_modules", "elfs", "library_list", "mountinfo", "module_extract"]) and "malware" not in name_lower:
                categories["文件/模块"].append(simple_name)
            elif any(x in name_lower for x in ["sockstat", "netfilter", "ip.addr", "ip.link"]) and "malware" not in name_lower:
                categories["网络相关"].append(simple_name)
            elif any(x in name_lower for x in ["malfind", "proc.maps", "vmaregexscan", "pagecache"]) and "malware" not in name_lower:
                categories["内存/恶意代码"].append(simple_name)
            elif any(x in name_lower for x in ["check_syscall", "check_afinfo", "check_idt", "check_modules", "check_creds", "capabilities", "keyboard_notifiers", "tty_check", "ebpf"]) and "malware" not in name_lower:
                categories["安全检查/Rootkit"].append(simple_name)
            elif any(x in name_lower for x in ["bash", "kmsg", "boottime", "kallsyms", "iomem", "vmcoreinfo", "pidhashtable", "fbdev"]):
                categories["系统信息/调试"].append(simple_name)
            elif any(x in name_lower for x in ["ftrace", "tracepoints", "perf_events", "pscallstack", "ptrace"]):
                categories["追踪/调试"].append(simple_name)
            elif "malware" in name_lower:
                categories["Malware专项"].append(simple_name)
            else:
                categories["其他"].append(simple_name)

        result = {}
        for category, plugins in categories.items():
            if plugins:
                result[category] = sorted(plugins)

        return result
