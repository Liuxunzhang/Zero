"""Allowlisted runtime settings persisted outside source-controlled config.py."""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from zero import config

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SETTINGS_FILE = _PROJECT_ROOT / ".zero" / "runtime_settings.json"
_SETTINGS_LOCK = threading.RLock()


@dataclass(frozen=True)
class SettingSpec:
    key: str
    config_name: str
    category: str
    label: str
    description: str
    value_type: str
    default: Any
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    unit: str = ""
    scale: float = 1.0
    options: tuple[str, ...] = ()

    def public_metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("config_name")
        data.pop("category")
        data.pop("scale")
        data["type"] = data.pop("value_type")
        data["min"] = data.pop("minimum")
        data["max"] = data.pop("maximum")
        data["options"] = list(data["options"])
        return data


_WORKER_METHODS = tuple(mp.get_all_start_methods()) or (
    "spawn" if sys.platform == "darwin" else "fork",
)

_SPECS = (
    # Plugin execution and worker liveness.
    SettingSpec(
        "plugin_timeout_seconds",
        "PLUGIN_TIMEOUT_SECONDS",
        "runtime",
        "插件总超时",
        "单次插件允许执行的最长时间。大镜像或低配置主机建议设为 1800 秒。",
        "integer",
        600,
        60,
        86400,
        30,
        "秒",
    ),
    SettingSpec(
        "plugin_stall_timeout_seconds",
        "PLUGIN_STALL_TIMEOUT_SECONDS",
        "runtime",
        "无响应超时",
        "工作进程连续无心跳或输出多久后中断；0 表示关闭该检测。",
        "integer",
        120,
        0,
        86400,
        15,
        "秒",
    ),
    SettingSpec(
        "worker_heartbeat_seconds",
        "WORKER_HEARTBEAT_SECONDS",
        "runtime",
        "工作进程心跳",
        "Volatility 静默扫描时的内部心跳间隔，必须小于启用状态下的无响应超时。",
        "number",
        15,
        1,
        300,
        1,
        "秒",
    ),
    SettingSpec(
        "progress_log_throttle_seconds",
        "PROGRESS_LOG_THROTTLE_SECONDS",
        "runtime",
        "进度日志节流",
        "两条进度消息之间的最短间隔；调大可减少日志刷新频率。",
        "number",
        0.5,
        0,
        10,
        0.1,
        "秒",
    ),
    SettingSpec(
        "terminate_grace_seconds",
        "TERMINATE_GRACE_SECONDS",
        "runtime",
        "停止宽限时间",
        "终止插件时，发送 terminate 后等待强制 kill 的时间。",
        "number",
        2,
        0,
        30,
        0.5,
        "秒",
    ),
    SettingSpec(
        "worker_start_method",
        "WORKER_START_METHOD",
        "runtime",
        "子进程启动方式",
        "Linux 通常使用 fork；出现库兼容问题时可尝试 spawn。",
        "select",
        "spawn" if sys.platform == "darwin" else "fork",
        options=_WORKER_METHODS,
    ),
    SettingSpec(
        "log_level",
        "LOG_LEVEL",
        "runtime",
        "后端日志级别",
        "调整控制台和日志文件的详细程度。",
        "select",
        "INFO",
        options=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    # Automatic Linux symbol preparation.
    SettingSpec(
        "auto_download_linux_symbols",
        "AUTO_DOWNLOAD_LINUX_SYMBOLS_ON_LOAD",
        "symbols",
        "加载后自动准备符号表",
        "识别 Linux kernel banner，并自动下载最匹配的 ISF。",
        "boolean",
        True,
    ),
    SettingSpec(
        "auto_symbol_scan_max_mb",
        "AUTO_SYMBOL_SCAN_MAX_BYTES",
        "symbols",
        "Kernel banner 扫描上限",
        "只扫描镜像前 N MB；0 表示扫描到发现 banner 或文件末尾。",
        "integer",
        0,
        0,
        1048576,
        64,
        "MB",
        1024 * 1024,
    ),
    SettingSpec(
        "auto_symbol_scan_chunk_mb",
        "AUTO_SYMBOL_SCAN_CHUNK_BYTES",
        "symbols",
        "Banner 扫描块大小",
        "流式读取镜像时每个数据块的大小。",
        "integer",
        4,
        1,
        64,
        1,
        "MB",
        1024 * 1024,
    ),
    SettingSpec(
        "auto_symbol_max_candidates",
        "AUTO_SYMBOL_DOWNLOAD_MAX_CANDIDATES",
        "symbols",
        "自动下载候选上限",
        "同一内核 release 的最佳候选超过此数量时不自动下载。",
        "integer",
        4,
        1,
        50,
        1,
        "个",
    ),
    SettingSpec(
        "symbol_index_ttl_minutes",
        "SYMBOL_INDEX_TTL_SECONDS",
        "symbols",
        "远程索引刷新间隔",
        "在此时间内复用内存或磁盘索引，不请求 GitHub API。",
        "integer",
        360,
        1,
        10080,
        30,
        "分钟",
        60,
    ),
    SettingSpec(
        "symbol_index_stale_hours",
        "SYMBOL_INDEX_STALE_SECONDS",
        "symbols",
        "陈旧索引可用时间",
        "GitHub 刷新失败时，允许继续使用旧索引的最长时间。",
        "integer",
        168,
        1,
        8760,
        24,
        "小时",
        3600,
    ),
    # Result memory, filter/sort, export, and disk cache limits.
    SettingSpec(
        "enable_disk_cache",
        "ENABLE_DISK_CACHE",
        "cache",
        "启用结果磁盘缓存",
        "保存插件结果，以便重启后或重复运行时直接复用。",
        "boolean",
        True,
    ),
    SettingSpec(
        "results_memory_cache_max",
        "RESULTS_MEMORY_CACHE_MAX",
        "cache",
        "内存结果缓存数",
        "进程内最多保留多少个完整插件结果；其余结果仍可从磁盘恢复。",
        "integer",
        8,
        1,
        128,
        1,
        "项",
    ),
    SettingSpec(
        "results_query_cache_max",
        "RESULTS_QUERY_CACHE_MAX",
        "cache",
        "筛选/排序缓存数",
        "最多保留多少种筛选和排序结果。",
        "integer",
        64,
        1,
        512,
        1,
        "项",
    ),
    SettingSpec(
        "results_query_cache_max_rows",
        "RESULTS_QUERY_CACHE_MAX_ROWS",
        "cache",
        "筛选缓存总行数",
        "所有筛选/排序缓存允许占用的总行数预算。",
        "integer",
        2000000,
        1000,
        50000000,
        10000,
        "行",
    ),
    SettingSpec(
        "max_table_rows",
        "MAX_TABLE_ROWS",
        "cache",
        "单次导出行数上限",
        "导出结果的软上限；0 表示不限制。",
        "integer",
        10000,
        0,
        50000000,
        1000,
        "行",
    ),
)

_SPEC_BY_KEY = {spec.key: spec for spec in _SPECS}
_CATEGORIES = (
    {
        "id": "runtime",
        "label": "运行与超时",
        "description": "控制 Volatility 子进程、超时、心跳和后端日志。",
    },
    {
        "id": "symbols",
        "label": "符号表",
        "description": "控制 Linux 内核识别、自动下载和远程索引缓存。",
    },
    {
        "id": "cache",
        "label": "结果与缓存",
        "description": "平衡重复分析速度、内存占用、磁盘缓存和导出规模。",
    },
)


def _coerce_ui_value(spec: SettingSpec, value: Any) -> Any:
    if spec.value_type == "boolean":
        if isinstance(value, bool):
            parsed = value
        elif isinstance(value, str):
            normalized = value.strip().lower()
            if normalized not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                raise ValueError(f"{spec.label} 必须是布尔值")
            parsed = normalized in {"true", "1", "yes", "on"}
        else:
            parsed = bool(value)
    elif spec.value_type == "integer":
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{spec.label} 必须是整数")
        if not number.is_integer():
            raise ValueError(f"{spec.label} 必须是整数")
        parsed = int(number)
    elif spec.value_type == "number":
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{spec.label} 必须是数字")
    elif spec.value_type == "select":
        parsed = str(value or "").strip()
        if parsed not in spec.options:
            raise ValueError(f"{spec.label} 必须是: {', '.join(spec.options)}")
    else:
        raise ValueError(f"不支持的设置类型: {spec.value_type}")

    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        if spec.minimum is not None and parsed < spec.minimum:
            raise ValueError(f"{spec.label} 不能小于 {spec.minimum:g}{spec.unit}")
        if spec.maximum is not None and parsed > spec.maximum:
            raise ValueError(f"{spec.label} 不能大于 {spec.maximum:g}{spec.unit}")
    return parsed


def _config_to_ui(spec: SettingSpec) -> Any:
    default_config_value = (
        spec.default * spec.scale
        if isinstance(spec.default, (int, float)) and not isinstance(spec.default, bool)
        else spec.default
    )
    value = getattr(config, spec.config_name, default_config_value)
    if spec.scale != 1 and isinstance(value, (int, float)):
        value = value / spec.scale
    if spec.value_type == "integer":
        return int(value)
    if spec.value_type == "number":
        return float(value)
    if spec.value_type == "boolean":
        return bool(value)
    return str(value)


def _ui_to_config(spec: SettingSpec, value: Any) -> Any:
    if spec.scale != 1 and isinstance(value, (int, float)):
        return int(round(value * spec.scale))
    return value


def _validate_relations(values: dict[str, Any]) -> None:
    stall = int(values["plugin_stall_timeout_seconds"])
    heartbeat = float(values["worker_heartbeat_seconds"])
    if stall > 0 and heartbeat >= stall:
        raise ValueError("工作进程心跳必须小于无响应超时；或将无响应超时设为 0")

    ttl_seconds = int(values["symbol_index_ttl_minutes"]) * 60
    stale_seconds = int(values["symbol_index_stale_hours"]) * 3600
    if stale_seconds < ttl_seconds:
        raise ValueError("陈旧索引可用时间不能短于远程索引刷新间隔")


def current_settings() -> dict[str, Any]:
    return {spec.key: _config_to_ui(spec) for spec in _SPECS}


def _apply_config_values(values: dict[str, Any]) -> None:
    for key, value in values.items():
        spec = _SPEC_BY_KEY[key]
        setattr(config, spec.config_name, _ui_to_config(spec, value))

    level_name = str(values.get("log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        handler.setLevel(level)


def _apply_live_settings() -> None:
    """Refresh an already-created engine without forcing lazy initialization."""
    try:
        from zero.engines import manager as manager_module

        manager = manager_module._MANAGER
        if manager is not None:
            manager.apply_runtime_settings("vol3")
    except Exception:
        logger.warning("Could not apply runtime settings to the live Vol3 engine", exc_info=True)


def _write_settings(values: dict[str, Any]) -> None:
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "settings": values,
    }
    tmp = _SETTINGS_FILE.with_name(
        f"{_SETTINGS_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, _SETTINGS_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def load_runtime_settings() -> dict[str, Any]:
    """Apply the persisted overlay before engines and services are initialized."""
    with _SETTINGS_LOCK:
        if not _SETTINGS_FILE.is_file():
            return current_settings()
        try:
            payload = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            saved = payload.get("settings", {}) if isinstance(payload, dict) else {}
            values = current_settings()
            for key, value in saved.items():
                spec = _SPEC_BY_KEY.get(key)
                if spec is not None:
                    values[key] = _coerce_ui_value(spec, value)
            _validate_relations(values)
            _apply_config_values(values)
            logger.info("Loaded runtime settings from %s", _SETTINGS_FILE)
        except Exception as exc:
            logger.warning("Ignored invalid runtime settings file %s: %s", _SETTINGS_FILE, exc)
        return current_settings()


def save_runtime_settings(updates: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(updates, dict) or not updates:
        raise ValueError("settings 不能为空")
    unknown = sorted(set(updates) - set(_SPEC_BY_KEY))
    if unknown:
        raise ValueError(f"不支持的设置项: {', '.join(unknown)}")

    with _SETTINGS_LOCK:
        values = current_settings()
        for key, value in updates.items():
            values[key] = _coerce_ui_value(_SPEC_BY_KEY[key], value)
        _validate_relations(values)
        _write_settings(values)
        _apply_config_values(values)
        _apply_live_settings()
        return values


def settings_payload() -> dict[str, Any]:
    categories = []
    for category in _CATEGORIES:
        categories.append(
            {
                **category,
                "fields": [
                    spec.public_metadata()
                    for spec in _SPECS
                    if spec.category == category["id"]
                ],
            }
        )
    try:
        storage = str(_SETTINGS_FILE.relative_to(_PROJECT_ROOT))
    except ValueError:
        storage = str(_SETTINGS_FILE)
    return {
        "settings": current_settings(),
        "categories": categories,
        "storage": storage,
        "effective": "immediate",
    }
