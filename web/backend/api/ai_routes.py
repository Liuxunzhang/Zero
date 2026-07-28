"""AI analysis SSE streaming routes + profile / prompt management."""

import asyncio
import difflib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from web.backend.services.ai_service import (
    AGENT_DENIED_PLUGINS,
    AGENT_HEAVY_PLUGINS,
    AGENT_PLUGIN_DESCRIPTIONS,
    get_ai_service,
)
from web.backend.services.tool_markup import strip_dsml_tool_markup
from web.backend.services.vol_service import get_service
from web.backend.services import conversation_store as conv_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_AI_DUMP_ROOT = _PROJECT_ROOT / "dumps" / "ai_agent"
_AGENT_PLUGIN_FALLBACK_ARG_ALLOWLIST = {
    "pid",
    "offset",
    "base",
    "key",
    "name",
    "ignore-case",
    "physical",
    "kernel_module",
    "regex",
}
_AGENT_PLUGIN_ARG_DENYLIST = {
    "dump",
    "dump_dir",
    "output",
    "output_dir",
    "yara_file",
    "yara_compiled_file",
    "strings_file",
}
_AGENT_PLUGIN_ARG_ALIASES = {
    "ignore_case": "ignore-case",
    "kernel-module": "kernel_module",
}
_AGENT_PLUGIN_ALIASES = {
    # Linux Volatility does not provide NetScan/NetStat.  These are the
    # equivalent installed socket plugins commonly requested by models trained
    # on Windows-oriented Volatility examples.
    ("linux", "netscan"): ("linux.sockscan.Sockscan", {}),
    ("linux", "netstat"): ("linux.sockstat.Sockstat", {}),
    # A Linux process thread listing is exposed by PsList --threads.
    ("linux", "threads"): ("linux.pslist.PsList", {"threads": True}),
    # Historical/community plugin name used by older prompts.
    ("linux", "procmaps"): ("linux.proc.Maps", {}),
}


def _safe_path_segment(value: str, fallback: str = "target") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned[:80] or fallback


def _file_snapshot(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    return {
        str(p.resolve())
        for p in directory.rglob("*")
        if p.is_file()
    }


def _new_files_after(directory: Path, before: set[str]) -> list[dict[str, Any]]:
    files = []
    for path_str in sorted(_file_snapshot(directory) - before):
        p = Path(path_str)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        files.append({"path": path_str, "size_bytes": size})
    return files


def _ensure_under_directory(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("Resolved path escapes the allowed directory")


def _column_index(columns: list[str], candidates: set[str]) -> Optional[int]:
    normalized = {
        str(name).strip().lower().replace(" ", "").replace("_", ""): idx
        for idx, name in enumerate(columns or [])
    }
    for candidate in candidates:
        idx = normalized.get(candidate)
        if idx is not None:
            return idx
    return None


def _normalize_process_name(value: Any) -> str:
    return str(value or "").strip().lower().removesuffix(".exe")


def _parse_int_value(value: Any) -> Optional[int]:
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return None


def _normalize_plugin_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _flatten_plugin_catalog(categories: dict, os_family: str) -> list[str]:
    plugins: list[str] = []
    seen: set[str] = set()
    for category_plugins in (categories or {}).values():
        for plugin in category_plugins or []:
            full_name = str(plugin)
            if not full_name.startswith(("linux.", "windows.", "mac.")):
                full_name = f"{os_family}.{full_name}"
            if full_name not in seen:
                plugins.append(full_name)
                seen.add(full_name)
    return plugins


def _resolve_agent_plugin_name(
    requested_name: str,
    installed_plugins: list[str],
    os_family: str,
) -> tuple[str, dict[str, Any], str]:
    """Resolve model-generated aliases against the actual installed catalogue."""
    requested_name = str(requested_name or "").strip()
    if not requested_name:
        raise ValueError("plugin_name is required")

    if requested_name.startswith(("linux.", "windows.", "mac.")):
        requested_full = requested_name
    else:
        requested_full = f"{os_family}.{requested_name}"

    by_lower = {name.lower(): name for name in installed_plugins}
    exact = by_lower.get(requested_full.lower())
    if exact:
        return exact, {}, "exact"

    normalized_requested = _normalize_plugin_token(requested_full)
    normalized_matches = [
        name
        for name in installed_plugins
        if _normalize_plugin_token(name) == normalized_requested
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0], {}, "normalized"

    short_requested = requested_full.split(".", 1)[1]
    requested_parts = short_requested.split(".")
    requested_module = ".".join(requested_parts[:-1]) if len(requested_parts) > 1 else short_requested
    requested_module_tail = requested_module.rsplit(".", 1)[-1]

    alias = _AGENT_PLUGIN_ALIASES.get(
        (os_family, _normalize_plugin_token(requested_module_tail))
    )
    if alias:
        alias_name, alias_args = alias
        alias_match = next(
            (
                name
                for name in installed_plugins
                if _normalize_plugin_token(name) == _normalize_plugin_token(alias_name)
            ),
            None,
        )
        if alias_match:
            return alias_match, dict(alias_args), "alias"

    # Class naming changed across Volatility releases (for example
    # CheckModules -> Check_modules and TtyCheck -> tty_check).  If the module
    # path is unambiguous, trust the installed class name.
    module_matches: list[tuple[int, str]] = []
    for installed_name in installed_plugins:
        installed_short = installed_name.split(".", 1)[1]
        installed_parts = installed_short.split(".")
        installed_module = (
            ".".join(installed_parts[:-1])
            if len(installed_parts) > 1
            else installed_short
        )
        installed_tail = installed_module.rsplit(".", 1)[-1]
        if _normalize_plugin_token(installed_module) == _normalize_plugin_token(requested_module):
            module_matches.append((0, installed_name))
        elif _normalize_plugin_token(installed_tail) == _normalize_plugin_token(requested_module_tail):
            module_matches.append((installed_module.count(".") + 1, installed_name))

    if module_matches:
        module_matches.sort(key=lambda item: (item[0], len(item[1]), item[1]))
        return module_matches[0][1], {}, "module"

    short_names = [name.split(".", 1)[1] for name in installed_plugins]
    suggestions = difflib.get_close_matches(
        short_requested,
        short_names,
        n=5,
        cutoff=0.25,
    )
    suffix = f"；相近的已安装插件: {', '.join(suggestions)}" if suggestions else ""
    raise ValueError(
        f"插件 '{requested_name}' 未安装或不属于当前 {os_family} 镜像{suffix}。"
        "请先调用 list_plugins，并使用其返回的精确名称。"
    )


def _coerce_plugin_arg(key: str, value: Any, arg_type: str = "") -> Any:
    if key in {"pid", "pids"} and isinstance(value, (list, tuple, set)):
        parsed_values = []
        for item in value:
            parsed = _parse_int_value(item)
            if parsed is None:
                raise ValueError(f"{key} must contain only integer or hex PID values")
            parsed_values.append(parsed)
        if not parsed_values:
            raise ValueError(f"{key} must contain at least one PID")
        return parsed_values
    if arg_type == "int" or key in {
        "pid",
        "offset",
        "base",
        "address",
        "inode",
        "netns",
        "maxsize",
        "max_size",
        "dump-size",
    }:
        parsed = _parse_int_value(value)
        if parsed is None:
            raise ValueError(f"{key} must be an integer or hex string")
        return parsed
    if arg_type == "bool" or key in {
        "ignore-case",
        "physical",
        "kernel_module",
    }:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (list, tuple)):
        return list(value)
    if arg_type == "string" or key in {"key", "name", "regex"}:
        return str(value)
    return value


def _extract_agent_plugin_args(
    tool_args: dict[str, Any],
    plugin_metadata: Optional[dict[str, Any]] = None,
    injected_args: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    raw_args = tool_args.get("args")
    merged: dict[str, Any] = {}
    if isinstance(raw_args, dict):
        merged.update(raw_args)
    for key, value in tool_args.items():
        if key not in {"plugin_name", "args"}:
            merged[key] = value
    if injected_args:
        merged.update(injected_args)

    definitions = {
        str(arg.get("name")): arg
        for arg in (plugin_metadata or {}).get("args", [])
        if arg.get("name")
    }
    normalized_names = {
        _normalize_plugin_token(name): name
        for name in definitions
    }
    allowed_names = (
        set(definitions)
        if definitions
        else set(_AGENT_PLUGIN_FALLBACK_ARG_ALLOWLIST)
    )

    kwargs: dict[str, Any] = {}
    for raw_key, raw_value in merged.items():
        key = _AGENT_PLUGIN_ARG_ALIASES.get(str(raw_key), str(raw_key))
        key = normalized_names.get(_normalize_plugin_token(key), key)
        if key == "pid" and "pid" not in definitions and "pids" in definitions:
            key = "pids"
        elif key == "pids" and "pids" not in definitions and "pid" in definitions:
            key = "pid"
        if key in _AGENT_PLUGIN_ARG_DENYLIST:
            if raw_value not in (None, "", False, 0, "false", "0"):
                raise ValueError(
                    f"参数 '{key}' 会读取或写入服务器文件，run_plugin 不允许使用；"
                    "请改用专用 dump 工具。"
                )
            continue
        if key not in allowed_names or raw_value in (None, ""):
            continue
        arg_type = str(definitions.get(key, {}).get("arg_type") or "")
        kwargs[key] = _coerce_plugin_arg(key, raw_value, arg_type)

    missing = [
        name
        for name, definition in definitions.items()
        if definition.get("required") and name not in kwargs
    ]
    if missing:
        raise ValueError(f"插件缺少必填参数: {', '.join(missing)}")
    return kwargs


def _agent_plugin_block_reason(
    plugin_name: str,
    plugin_metadata: Optional[dict[str, Any]] = None,
) -> str:
    denied_names = {
        _normalize_plugin_token(name)
        for name in AGENT_DENIED_PLUGINS
    }
    if _normalize_plugin_token(plugin_name) in denied_names:
        return "该插件会提取文件，必须通过专用 dump 工具执行"
    required_file_args = [
        str(arg.get("name"))
        for arg in (plugin_metadata or {}).get("args", [])
        if arg.get("required") and str(arg.get("name")) in _AGENT_PLUGIN_ARG_DENYLIST
    ]
    if required_file_args:
        return f"必填参数涉及服务器文件: {', '.join(required_file_args)}"
    return ""


def _safe_plugin_arg_details(plugin_metadata: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(arg.get("name")),
            "type": str(arg.get("arg_type") or "string"),
            "required": bool(arg.get("required")),
        }
        for arg in (plugin_metadata or {}).get("args", [])
        if arg.get("name") and str(arg.get("name")) not in _AGENT_PLUGIN_ARG_DENYLIST
    ]


def _apply_agent_argument_compatibility(
    plugin_name: str,
    kwargs: dict[str, Any],
    tool_args: dict[str, Any],
) -> None:
    """Translate common model options that differ from Volatility's metadata."""
    raw_args = tool_args.get("args")
    combined = dict(raw_args) if isinstance(raw_args, dict) else {}
    combined.update({
        key: value
        for key, value in tool_args.items()
        if key not in {"plugin_name", "args"}
    })
    ignore_case = combined.get("ignore-case", combined.get("ignore_case"))
    regex_scanners = {
        "linux.vmaregexscan.VmaRegExScan",
        "windows.vadregexscan.VadRegExScan",
    }
    if (
        plugin_name in regex_scanners
        and str(ignore_case).strip().lower() in {"1", "true", "yes", "on"}
        and kwargs.get("pattern")
        and not str(kwargs["pattern"]).startswith("(?i)")
    ):
        kwargs["pattern"] = f"(?i){kwargs['pattern']}"


def _extract_process_matches(
    columns: list[str],
    rows: list,
    process_name: str,
    max_matches: int,
) -> list[dict[str, Any]]:
    pid_idx = _column_index(columns, {"pid", "processid"})
    name_idx = _column_index(
        columns,
        {"imagefilename", "image", "name", "process", "comm"},
    )
    ppid_idx = _column_index(columns, {"ppid", "parentpid"})
    if pid_idx is None or name_idx is None:
        return []

    target = _normalize_process_name(process_name)
    matches: list[dict[str, Any]] = []
    for row in rows or []:
        values = list(row)
        if pid_idx >= len(values) or name_idx >= len(values):
            continue
        name = str(values[name_idx] or "")
        normalized_name = _normalize_process_name(name)
        if not target or not normalized_name:
            continue
        if target == normalized_name or target in normalized_name or normalized_name in target:
            try:
                pid = int(str(values[pid_idx]), 0)
            except (TypeError, ValueError):
                continue
            match = {"pid": pid, "name": name}
            if ppid_idx is not None and ppid_idx < len(values):
                match["ppid"] = str(values[ppid_idx])
            matches.append(match)
            if len(matches) >= max_matches:
                break
    return matches


def _agent_pid_candidates(
    columns: list[str],
    rows: list,
    limit: int = 200,
) -> list[dict[str, Any]]:
    pid_idx = _column_index(columns, {"pid", "processid"})
    if pid_idx is None:
        return []
    name_idx = _column_index(
        columns,
        {"imagefilename", "image", "name", "process", "comm"},
    )
    ppid_idx = _column_index(columns, {"ppid", "parentpid"})
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()

    for row in rows or []:
        values = list(row)
        if pid_idx >= len(values):
            continue
        pid = _parse_int_value(values[pid_idx])
        if pid is None or pid in seen:
            continue
        candidate: dict[str, Any] = {"pid": pid}
        if name_idx is not None and name_idx < len(values):
            candidate["name"] = str(values[name_idx] or "")
        if ppid_idx is not None and ppid_idx < len(values):
            ppid = _parse_int_value(values[ppid_idx])
            if ppid is not None:
                candidate["ppid"] = ppid
        candidates.append(candidate)
        seen.add(pid)
        if len(candidates) >= limit:
            break
    return candidates


def _extract_module_matches(
    columns: list[str],
    rows: list,
    module_name: str,
    max_matches: int,
) -> list[dict[str, Any]]:
    base_idx = _column_index(columns, {"base", "baseaddress", "dllbase"})
    name_idx = _column_index(columns, {"name", "module", "imagename", "imagefilename"})
    path_idx = _column_index(columns, {"path", "filepath", "fullpath"})
    size_idx = _column_index(columns, {"size", "imagesize"})
    if base_idx is None:
        return []

    target = _normalize_process_name(module_name)
    matches: list[dict[str, Any]] = []
    for row in rows or []:
        values = list(row)
        if base_idx >= len(values):
            continue

        name = str(values[name_idx] or "") if name_idx is not None and name_idx < len(values) else ""
        path = str(values[path_idx] or "") if path_idx is not None and path_idx < len(values) else ""
        haystack = " ".join([_normalize_process_name(name), _normalize_process_name(path)])
        if target and target not in haystack:
            continue

        base = _parse_int_value(values[base_idx])
        if base is None:
            continue
        match: dict[str, Any] = {"base": base, "base_hex": hex(base), "name": name, "path": path}
        if size_idx is not None and size_idx < len(values):
            parsed_size = _parse_int_value(values[size_idx])
            match["size"] = parsed_size if parsed_size is not None else str(values[size_idx])
        matches.append(match)
        if len(matches) >= max_matches:
            break
    return matches


# ── Request models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    include_context: bool = True
    engine: str = "vol3"
    conversation_id: Optional[str] = None   # if None, auto-create a new conversation
    mode: str = "agent"   # "chat" (对话模式) or "agent" (智能体模式)
    os_family: Optional[str] = None


class ProfileModel(BaseModel):
    id: str = ""
    name: str
    base_url: str
    api_key: str = ""
    model: str


class SaveProfilesRequest(BaseModel):
    profiles: list[ProfileModel]


class SetActiveProfileRequest(BaseModel):
    profile: Optional[ProfileModel] = None   # None = use config.py


class PromptModel(BaseModel):
    id: str = ""
    name: str
    content: str


class SetActivePromptRequest(BaseModel):
    prompt_id: str


class PersistConfigRequest(BaseModel):
    persist_to_config_py: bool


class AiSettingsRequest(BaseModel):
    ai_max_tokens: int | str | None = None
    ai_temperature: float | None = None
    ai_max_history: int | None = None
    ai_context_max_rows: int | str | None = None
    ai_context_max_chars: int | None = None
    ai_memory_enabled: bool | None = None
    ai_memory_max_chars: int | None = None
    ai_memory_retrieval_top_k: int | None = None
    ai_memory_retrieval_max_chars: int | None = None
    ai_memory_ttl_days: int | None = None


# ── Agent tool executor ─────────────────────────────────────────────

async def _execute_agent_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    engine_id: str,
    os_family: str = "linux",
) -> dict[str, Any]:
    """Execute a tool requested by the AI agent.

    *os_family* should reflect the **actual** OS of the loaded image
    (``"windows"`` or ``"linux"``) so that ``list_plugins`` defaults
    correctly and ``run_plugin`` can prepend the right prefix.
    """
    if engine_id != "vol3" and tool_name in {"dump_process", "dump_pe"}:
        raise ValueError(f"Tool {tool_name} is only available for the Volatility 3 engine")
    svc = get_service()
    mgr = svc._manager
    loop = asyncio.get_event_loop()

    # ── list_plugins ──────────────────────────────────────────────
    if tool_name == "list_plugins":
        # Always use the image's actual OS — ignore whatever the AI guesses.
        categories = await loop.run_in_executor(
            None,
            lambda: mgr.list_plugins(engine_id, os_family),
        )
        engine = mgr.get_engine(engine_id)
        metadata_getter = getattr(engine, "get_plugin_metadata", None)
        executable_categories: dict[str, list[str]] = {}
        plugin_details: list[dict[str, Any]] = []
        blocked_plugins: list[dict[str, str]] = []
        flat: list[str] = []
        for cat, plugins in (categories or {}).items():
            plugin_strs = []
            for p in plugins:
                full_p = (
                    p if engine_id != "vol3" or p.startswith(f"{os_family}.")
                    else f"{os_family}.{p}"
                )
                metadata = metadata_getter(full_p) if callable(metadata_getter) else {}
                metadata = metadata or {}
                block_reason = _agent_plugin_block_reason(full_p, metadata)
                if block_reason:
                    blocked_plugins.append({"plugin_name": p, "reason": block_reason})
                    continue

                executable_categories.setdefault(cat, []).append(p)
                desc = AGENT_PLUGIN_DESCRIPTIONS.get(full_p, "")
                safe_args = _safe_plugin_arg_details(metadata)
                if _normalize_plugin_token(full_p) in {
                    _normalize_plugin_token(name)
                    for name in AGENT_HEAVY_PLUGINS
                }:
                    for arg in safe_args:
                        if arg["name"] in {"pid", "pids"}:
                            arg["required"] = True
                required_args = [
                    arg["name"] for arg in safe_args if arg["required"]
                ]
                plugin_details.append({
                    "plugin_name": p,
                    "full_name": full_p,
                    "description": desc,
                    "arguments": safe_args,
                })
                required_suffix = (
                    f"; 必填参数: {', '.join(required_args)}"
                    if required_args
                    else ""
                )
                if desc:
                    plugin_strs.append(f"{p} ({desc}{required_suffix})")
                else:
                    plugin_strs.append(f"{p}{required_suffix}")
            if plugin_strs:
                flat.append(f"[{cat}] {', '.join(plugin_strs)}")
        total = len(plugin_details)
        return {
            "os_family": os_family,
            "categories": executable_categories,
            "plugins": plugin_details,
            "blocked_plugins": blocked_plugins,
            "summary": (
                f"AI 可执行的已安装 {os_family} 插件共 {total} 个。"
                "调用 run_plugin 时必须原样使用下列 plugin_name，"
                "不要改变大小写、下划线或类名：\n"
                + "\n".join(flat)
            ),
        }

    # ── run_plugin ────────────────────────────────────────────────
    if tool_name == "run_plugin":
        plugin_name = str(tool_args.get("plugin_name", ""))
        if not plugin_name:
            raise ValueError("plugin_name is required")

        engine = mgr.get_engine(engine_id)
        categories = await loop.run_in_executor(
            None,
            lambda: mgr.list_plugins(engine_id, os_family),
        )
        if engine_id == "vol3":
            installed_plugins = _flatten_plugin_catalog(categories, os_family)
            plugin_name, injected_args, resolution = _resolve_agent_plugin_name(
                plugin_name,
                installed_plugins,
                os_family,
            )
        else:
            installed_plugins = [
                str(item)
                for values in (categories or {}).values()
                for item in (values or [])
            ]
            exact = next(
                (item for item in installed_plugins if item.casefold() == plugin_name.casefold()),
                None,
            )
            if exact is None:
                raise ValueError(
                    f"插件 '{plugin_name}' 未安装；请先调用 list_plugins 并复制精确名称。"
                )
            plugin_name, injected_args, resolution = exact, {}, "exact"
        if resolution != "exact":
            logger.info(
                "Resolved AI agent plugin name %s -> %s (%s)",
                tool_args.get("plugin_name"),
                plugin_name,
                resolution,
            )
        metadata_getter = getattr(engine, "get_plugin_metadata", None)
        plugin_metadata = metadata_getter(plugin_name) if callable(metadata_getter) else {}
        plugin_metadata = plugin_metadata or {}
        block_reason = _agent_plugin_block_reason(plugin_name, plugin_metadata)
        if block_reason:
            raise ValueError(f"插件 '{plugin_name}' 不允许由 AI Agent 直接执行：{block_reason}。")

        # Validate kwargs against this installed plugin's real metadata.
        # File-reading/writing arguments stay behind dedicated tools.
        kwargs = _extract_agent_plugin_args(
            tool_args,
            plugin_metadata,
            injected_args,
        )
        _apply_agent_argument_compatibility(plugin_name, kwargs, tool_args)
        pid = kwargs.get("pid")

        # Heavy scanners need a PID filter.  Instead of returning a dead-end
        # error, automatically enumerate processes and give the agent concrete
        # candidates so it can select and immediately retry by itself.
        short = plugin_name.split(".", 1)[1] if "." in plugin_name else plugin_name
        heavy_names = {
            _normalize_plugin_token(name)
            for name in AGENT_HEAVY_PLUGINS
        }
        if _normalize_plugin_token(plugin_name) in heavy_names and pid is None:
            discovery_error = ""
            discovery_plugin = ""
            discovery_columns: list[str] = []
            discovery_rows: list = []
            pid_candidates: list[dict[str, Any]] = []
            for process_plugin in ("pslist.PsList", "psscan.PsScan"):
                try:
                    discovery_plugin, _, _ = _resolve_agent_plugin_name(
                        process_plugin,
                        installed_plugins,
                        os_family,
                    )

                    def _discover(name=discovery_plugin) -> tuple:
                        return mgr.run_plugin(engine_id, name)

                    discovery_columns, discovery_rows = await loop.run_in_executor(
                        None,
                        _discover,
                    )
                    pid_candidates = _agent_pid_candidates(
                        discovery_columns,
                        discovery_rows,
                    )
                    if pid_candidates:
                        break
                except Exception as exc:
                    discovery_error = str(exc)

            if not pid_candidates:
                raise ValueError(
                    f"插件 {short} 需要 pid，且自动枚举进程未获得候选 PID。"
                    + (f"枚举错误: {discovery_error}" if discovery_error else "")
                )

            candidate_text = ", ".join(
                (
                    f"{item['pid']}({item.get('name') or '?'})"
                    if item.get("name")
                    else str(item["pid"])
                )
                for item in pid_candidates[:50]
            )
            return {
                "status": "pid_required",
                "plugin": plugin_name,
                "requested_plugin": str(tool_args.get("plugin_name") or ""),
                "discovery_plugin": discovery_plugin,
                "pid_candidates": pid_candidates,
                "candidate_count": len(pid_candidates),
                "retry_arguments": {
                    "plugin_name": short,
                    "args": kwargs,
                },
                "summary": (
                    f"插件 {short} 必须按 PID 扫描。系统已自动通过 "
                    f"{discovery_plugin} 枚举 {len(pid_candidates)} 个候选进程："
                    f"{candidate_text}。AI 必须自行选择相关 PID，并立即使用 "
                    "pid=<PID> 或 pids=[...] 重试原插件，不要停止或询问用户。"
                ),
            }

        def _run() -> tuple:
            return mgr.run_plugin(engine_id, plugin_name, **kwargs)

        columns, rows = await loop.run_in_executor(None, _run)
        total = len(rows)
        preview = [list(r) for r in rows[:100]]
        requested_plugin = str(tool_args.get("plugin_name") or "")
        resolution_note = (
            f"已将 {requested_plugin} 解析为已安装插件 {plugin_name}。"
            if resolution != "exact"
            else ""
        )
        return {
            "plugin": plugin_name,
            "requested_plugin": requested_plugin,
            "resolution": resolution,
            "arguments": kwargs,
            "columns": columns,
            "rows": preview,
            "total": total,
            "truncated": total > 100,
            "summary": (
                resolution_note
                + f"插件 {plugin_name} 返回 {total} 行, "
                f"{len(columns)} 列 ({', '.join(columns[:20])})"
                + ("（仅展示前 100 行）" if total > 100 else "")
            ),
        }

    # ── dump_process ──────────────────────────────────────────────
    if tool_name == "dump_process":
        if os_family not in {"linux", "windows"}:
            raise ValueError(f"dump_process does not support {os_family} memory images.")

        raw_name = str(tool_args.get("process_name") or "").strip()
        raw_pid = tool_args.get("pid")
        if not raw_name and raw_pid is None:
            raise ValueError("process_name or pid is required")

        try:
            max_matches = max(1, min(10, int(tool_args.get("max_matches") or 3)))
        except (TypeError, ValueError):
            max_matches = 3

        target_label = raw_name or f"pid_{raw_pid}"
        dump_dir = (
            _AI_DUMP_ROOT
            / _safe_path_segment(engine_id, "engine")
            / f"{int(time.time())}_{_safe_path_segment(target_label)}"
        ).resolve()
        root = _AI_DUMP_ROOT.resolve()
        _ensure_under_directory(dump_dir, root)
        dump_dir.mkdir(parents=True, exist_ok=True)

        matches: list[dict[str, Any]] = []
        discovery_plugin = ""
        if raw_pid is not None:
            parsed_pid = _parse_int_value(raw_pid)
            if parsed_pid is None:
                raise ValueError("pid must be an integer")
            matches = [{"pid": parsed_pid, "name": raw_name or str(parsed_pid)}]
        else:
            pslist_plugin = f"{os_family}.pslist.PsList"

            def _run_pslist() -> tuple:
                return mgr.run_plugin(engine_id, pslist_plugin)

            columns, rows = await loop.run_in_executor(None, _run_pslist)
            discovery_plugin = pslist_plugin
            matches = _extract_process_matches(columns, rows, raw_name, max_matches)

            if not matches:
                psscan_plugin = f"{os_family}.psscan.PsScan"

                def _run_psscan() -> tuple:
                    return mgr.run_plugin(engine_id, psscan_plugin)

                columns, rows = await loop.run_in_executor(None, _run_psscan)
                discovery_plugin = psscan_plugin
                matches = _extract_process_matches(columns, rows, raw_name, max_matches)

        if not matches:
            raise ValueError(f"No {os_family} process matched: {raw_name}")

        before = _file_snapshot(dump_dir)
        dump_results: list[dict[str, Any]] = []
        dump_plugin = (
            "windows.memmap.Memmap"
            if os_family == "windows"
            else "linux.pslist.PsList"
        )
        for match in matches:
            pid = int(match["pid"])

            def _dump_one(pid=pid) -> tuple:
                return mgr.run_plugin(
                    engine_id,
                    dump_plugin,
                    pid=pid,
                    dump=True,
                    dump_dir=str(dump_dir),
                    use_cache=False,
                )

            columns, rows = await loop.run_in_executor(None, _dump_one)
            dump_results.append({
                "plugin": dump_plugin,
                "pid": pid,
                "process_name": match.get("name", ""),
                "columns": columns,
                "total_rows": len(rows),
            })

        new_files = _new_files_after(dump_dir, before)
        return {
            "os_family": os_family,
            "target": raw_name,
            "discovery_plugin": discovery_plugin,
            "matches": matches,
            "dump_plugin": dump_plugin,
            "dump_dir": str(dump_dir),
            "files": new_files,
            "summary": (
                f"已 dump {len(matches)} 个匹配进程到 {dump_dir}，"
                f"新增文件 {len(new_files)} 个。"
            ),
            "details": dump_results,
        }

    # ── dump_pe ───────────────────────────────────────────────────
    if tool_name == "dump_pe":
        if os_family != "windows":
            raise ValueError("dump_pe currently supports Windows memory images only.")

        raw_name = str(tool_args.get("process_name") or "").strip()
        module_name = str(tool_args.get("module_name") or raw_name).strip()
        raw_pid = tool_args.get("pid")
        raw_base = tool_args.get("base")
        kernel_module = bool(tool_args.get("kernel_module", False))
        if not module_name and raw_base is None:
            raise ValueError("module_name/process_name or base is required")

        try:
            max_matches = max(1, min(10, int(tool_args.get("max_matches") or 3)))
        except (TypeError, ValueError):
            max_matches = 3

        target_label = module_name or raw_name or f"base_{raw_base}"
        dump_dir = (
            _AI_DUMP_ROOT
            / _safe_path_segment(engine_id, "engine")
            / f"{int(time.time())}_{_safe_path_segment(target_label)}_pedump"
        ).resolve()
        root = _AI_DUMP_ROOT.resolve()
        _ensure_under_directory(dump_dir, root)
        dump_dir.mkdir(parents=True, exist_ok=True)

        process_matches: list[dict[str, Any]] = []
        module_matches: list[dict[str, Any]] = []
        discovery_plugins: list[str] = []

        if raw_base is not None:
            parsed_base = _parse_int_value(raw_base)
            if parsed_base is None:
                raise ValueError("base must be an integer or hex string")
            module_matches = [{"base": parsed_base, "base_hex": hex(parsed_base), "name": module_name}]
            if raw_pid is not None:
                parsed_pid = _parse_int_value(raw_pid)
                if parsed_pid is None:
                    raise ValueError("pid must be an integer")
                process_matches = [{"pid": parsed_pid, "name": raw_name or str(parsed_pid)}]
        elif kernel_module:
            def _run_modules() -> tuple:
                return mgr.run_plugin(engine_id, "windows.modules.Modules")

            columns, rows = await loop.run_in_executor(None, _run_modules)
            discovery_plugins.append("windows.modules.Modules")
            module_matches = _extract_module_matches(columns, rows, module_name, max_matches)
        else:
            if raw_pid is not None:
                parsed_pid = _parse_int_value(raw_pid)
                if parsed_pid is None:
                    raise ValueError("pid must be an integer")
                process_matches = [{"pid": parsed_pid, "name": raw_name or str(parsed_pid)}]
            else:
                if not raw_name:
                    raise ValueError("process_name is required when pid is not provided")

                def _run_pslist() -> tuple:
                    return mgr.run_plugin(engine_id, "windows.pslist.PsList")

                columns, rows = await loop.run_in_executor(None, _run_pslist)
                discovery_plugins.append("windows.pslist.PsList")
                process_matches = _extract_process_matches(columns, rows, raw_name, max_matches)

                if not process_matches:
                    def _run_psscan() -> tuple:
                        return mgr.run_plugin(engine_id, "windows.psscan.PsScan")

                    columns, rows = await loop.run_in_executor(None, _run_psscan)
                    discovery_plugins.append("windows.psscan.PsScan")
                    process_matches = _extract_process_matches(columns, rows, raw_name, max_matches)

            if not process_matches:
                raise ValueError(f"No Windows process matched: {raw_name}")

            for proc in process_matches:
                pid = int(proc["pid"])

                def _run_dlllist(pid=pid) -> tuple:
                    return mgr.run_plugin(engine_id, "windows.dlllist.DllList", pid=pid)

                columns, rows = await loop.run_in_executor(None, _run_dlllist)
                if "windows.dlllist.DllList" not in discovery_plugins:
                    discovery_plugins.append("windows.dlllist.DllList")
                for match in _extract_module_matches(columns, rows, module_name, max_matches):
                    match["pid"] = pid
                    match["process_name"] = proc.get("name", "")
                    module_matches.append(match)
                    if len(module_matches) >= max_matches:
                        break
                if len(module_matches) >= max_matches:
                    break

        if not module_matches:
            raise ValueError(f"No PE module/base matched: {module_name or raw_base}")

        before = _file_snapshot(dump_dir)
        dump_results: list[dict[str, Any]] = []
        for match in module_matches[:max_matches]:
            base = int(match["base"])
            kwargs: dict[str, Any] = {
                "base": base,
                "dump_dir": str(dump_dir),
                "use_cache": False,
            }
            if kernel_module:
                kwargs["kernel_module"] = True
            elif match.get("pid") is not None:
                kwargs["pid"] = int(match["pid"])

            def _dump_one(kwargs=kwargs) -> tuple:
                return mgr.run_plugin(engine_id, "windows.pedump.PEDump", **kwargs)

            columns, rows = await loop.run_in_executor(None, _dump_one)
            dump_results.append({
                "plugin": "windows.pedump.PEDump",
                "pid": match.get("pid"),
                "process_name": match.get("process_name", ""),
                "module_name": match.get("name", ""),
                "base": match.get("base_hex", hex(base)),
                "columns": columns,
                "total_rows": len(rows),
            })

        new_files = _new_files_after(dump_dir, before)
        return {
            "os_family": os_family,
            "target": target_label,
            "discovery_plugins": discovery_plugins,
            "matches": module_matches[:max_matches],
            "dump_plugin": "windows.pedump.PEDump",
            "dump_dir": str(dump_dir),
            "files": new_files,
            "summary": (
                f"已用 PEDump dump {len(dump_results)} 个 PE 到 {dump_dir}，"
                f"新增文件 {len(new_files)} 个。"
            ),
            "details": dump_results,
        }

    raise ValueError(f"Unknown tool: {tool_name}")


# ── SSE streaming ──────────────────────────────────────────────────

async def _sse_stream(
    user_message: str,
    plugin_context: Optional[dict],
    conversation_id: str,
    tool_executor: Any = None,
    engine_id: str = "vol3",
    os_family: str = "linux",
):
    ai = get_ai_service()
    assistant_parts: list[str] = []
    messages_to_persist = []
    messages_to_persist.append({"role": "user", "content": user_message})
    try:
        async for event in ai.chat_stream(
            user_message,
            plugin_context,
            tool_executor=tool_executor,
            engine_id=engine_id,
            os_family=os_family,
            conversation_id=conversation_id,
        ):
            payload = json.dumps(event, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            
            ev_type = event.get("type")
            if ev_type == "chunk":
                assistant_parts.append(event.get("content", ""))
            elif ev_type == "tool_call":
                messages_to_persist.append({
                    "role": "tool",
                    "tool_call_id": event.get("tool_call_id"),
                    "toolName": event.get("tool_name"),
                    "toolArgs": event.get("arguments") or {},
                    "toolRunning": False,
                    "toolSummary": "",
                    "toolError": "",
                })
            elif ev_type == "tool_result":
                tc_id = event.get("tool_call_id")
                for msg in messages_to_persist:
                    if msg.get("role") == "tool" and msg.get("tool_call_id") == tc_id:
                        if event.get("ok"):
                            msg["toolSummary"] = event.get("summary") or ""
                        else:
                            msg["toolError"] = event.get("error") or "Unknown error"
                        break
        # Persist all turns to the conversation store.
        assistant_content = "".join(assistant_parts)
        if assistant_content:
            messages_to_persist.append({"role": "assistant", "content": assistant_content})
        
        conv_store.append_messages(conversation_id, messages_to_persist)
        yield (
            f"data: {json.dumps({'type': 'done', 'content': '', 'conversation_id': conversation_id})}\n\n"
        )
    except Exception as e:
        logger.error("SSE stream error: %s", e, exc_info=True)
        payload = json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False)
        yield f"data: {payload}\n\n"


@router.post("/chat")
async def ai_chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Message cannot be empty")

    ai = get_ai_service()
    cfg = ai.get_config_info()
    if not cfg["has_api_key"]:
        raise HTTPException(
            400,
            "未配置 AI API Key。请通过设置面板或 config.py 进行配置。",
        )

    plugin_context = None
    svc = get_service()
    if req.include_context:
        ai_settings = cfg.get("ai_settings", {}) if isinstance(cfg, dict) else {}
        limit = ai_settings.get("ai_context_max_rows", 200)
        engine_id = str(req.engine or "vol3")
        if isinstance(limit, str) and limit.strip().lower() == "max":
            plugin_context = svc.get_current_result_context(engine_id=engine_id)
        else:
            try:
                plugin_context = svc.get_current_result_context(
                    max_rows=int(limit), engine_id=engine_id
                )
            except (TypeError, ValueError):
                plugin_context = svc.get_current_result_context(max_rows=200, engine_id=engine_id)

    # Resolve or create the conversation to save into.
    conv_id = req.conversation_id
    if conv_id and not conv_store.get_conversation(conv_id):
        conv_id = None          # stale id — treat as new
    if not conv_id:
        meta = conv_store.create_conversation(engine=req.engine)
        conv_id = meta["id"]

    engine_id = str(req.engine or "vol3")

    # Detect the loaded image's OS family so the agent and system prompt
    # know whether to use linux.* or windows.* plugins.
    requested_os = str(req.os_family or "").strip().lower()
    if requested_os in {"linux", "windows"}:
        os_family = requested_os
    else:
        try:
            img_status = svc._manager.get_image_status(engine_id)
        except Exception:
            img_status = {}
        os_family = str(img_status.get("os_family", "linux") or "linux")

    # Build the agent tool executor (lazy — only invoked when the AI
    # actually calls a tool).
    async def _tool_executor(
        tool_name: str,
        tool_args: dict[str, Any],
        _engine_id: str,
    ) -> dict[str, Any]:
        return await _execute_agent_tool(tool_name, tool_args, _engine_id, os_family=os_family)

    tool_exec = _tool_executor if req.mode == "agent" else None

    return StreamingResponse(
        _sse_stream(
            message,
            plugin_context,
            conv_id,
            tool_executor=tool_exec,
            engine_id=engine_id,
            os_family=os_family,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── History ────────────────────────────────────────────────────────

@router.get("/history")
async def ai_history(conversation_id: Optional[str] = None):
    return {"history": get_ai_service().get_history(conversation_id)}


@router.delete("/history")
async def clear_ai_history(conversation_id: Optional[str] = None):
    get_ai_service().clear_history(conversation_id)
    return {"ok": True}


@router.delete("/memory")
async def clear_ai_memory():
    removed = get_ai_service().clear_all_memory()
    return {"ok": True, "removed_items": removed}


@router.get("/memory")
async def get_ai_memory():
    return {"compressed_memory": get_ai_service().get_compressed_memory()}


@router.get("/memory/stats")
async def get_ai_memory_stats():
    return {"stats": get_ai_service().get_memory_stats()}


@router.get("/config")
async def ai_config():
    return get_ai_service().get_config_info()


@router.get("/persist-config")
async def get_persist_config():
    return {"persist_to_config_py": get_ai_service().get_persist_to_config()}


@router.post("/persist-config")
async def set_persist_config(req: PersistConfigRequest):
    enabled = get_ai_service().set_persist_to_config(req.persist_to_config_py)
    return {"ok": True, "persist_to_config_py": enabled}


@router.get("/settings")
async def get_ai_settings():
    return {"settings": get_ai_service().get_ai_settings()}


@router.post("/settings")
async def save_ai_settings(req: AiSettingsRequest):
    payload = req.model_dump(exclude_none=True)
    settings = get_ai_service().save_ai_settings(payload)
    return {"ok": True, "settings": settings}


# ── Profiles ───────────────────────────────────────────────────────

@router.get("/profiles")
async def list_profiles():
    return {"profiles": get_ai_service().get_profiles()}


@router.post("/profiles")
async def save_profiles(req: SaveProfilesRequest):
    profiles = []
    for p in req.profiles:
        d = p.model_dump()
        if not d.get("id"):
            d["id"] = str(uuid.uuid4())[:8]
        profiles.append(d)
    get_ai_service().save_profiles(profiles)
    return {"ok": True, "count": len(profiles), "profiles": profiles}


@router.post("/profiles/active")
async def set_active_profile(req: SetActiveProfileRequest):
    if req.profile:
        get_ai_service().set_active_profile(req.profile.model_dump())
    else:
        get_ai_service().set_active_profile(None)
    return {"ok": True, "config": get_ai_service().get_config_info()}


@router.get("/profiles/active")
async def get_active_profile():
    p = get_ai_service().get_active_profile()
    return {"profile": p}


# ── Prompts ────────────────────────────────────────────────────────

@router.get("/prompts")
async def list_prompts():
    return {"prompts": get_ai_service().get_all_prompts()}


@router.post("/prompts")
async def save_prompt(req: PromptModel):
    d = req.model_dump()
    if not d.get("id"):
        d["id"] = "custom_" + str(uuid.uuid4())[:8]
    d["builtin"] = False
    get_ai_service().save_custom_prompt(d)
    return {"ok": True, "prompt": d}


@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str):
    if get_ai_service().delete_custom_prompt(prompt_id):
        return {"ok": True}
    raise HTTPException(404, "Prompt not found or is built-in")


@router.post("/prompts/active")
async def set_active_prompt(req: SetActivePromptRequest):
    get_ai_service().set_active_prompt(req.prompt_id)
    return {"ok": True, "active_prompt_id": req.prompt_id}


# ── Filter rule info (for AI context) ─────────────────────────────

@router.get("/filter-syntax")
async def filter_syntax():
    """Return filter syntax documentation for reference."""
    return {
        "operators": [
            {"op": "-eq", "desc": "Equal to"},
            {"op": "-ne", "desc": "Not equal to"},
            {"op": "-gt", "desc": "Greater than"},
            {"op": "-lt", "desc": "Less than"},
            {"op": "-ge", "desc": "Greater than or equal to"},
            {"op": "-le", "desc": "Less than or equal to"},
            {"op": "-contain", "desc": "Contains substring"},
            {"op": "-notcontain", "desc": "Does not contain"},
            {"op": "-match", "desc": "Regex match"},
            {"op": "-startswith", "desc": "Starts with"},
            {"op": "-endswith", "desc": "Ends with"},
        ],
        "logic": ["&&", "||"],
        "example": 'ImageFileName -contain "svchost" && PID -gt 100',
        "current_columns": get_service().get_current_columns(engine_id="vol3"),
    }


# ── Conversations (persistent history) ────────────────────────────

class RenameConversationRequest(BaseModel):
    title: str


class CreateConversationRequest(BaseModel):
    title: str = ""
    engine: str = "vol3"


@router.get("/conversations")
async def list_conversations():
    """Return all saved conversations, newest first."""
    return {"conversations": conv_store.list_conversations()}


@router.post("/conversations")
async def create_conversation(req: CreateConversationRequest):
    """Explicitly create a new empty conversation."""
    meta = conv_store.create_conversation(title=req.title, engine=req.engine)
    return {"ok": True, "conversation": meta}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Return full message transcript for a conversation."""
    meta = conv_store.get_conversation(conv_id)
    if not meta:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    messages = conv_store.get_messages(conv_id)
    messages = [
        {
            **message,
            "content": strip_dsml_tool_markup(message.get("content") or ""),
        }
        if message.get("role") == "assistant"
        else message
        for message in messages
    ]
    return {"conversation": meta, "messages": messages}


@router.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, req: RenameConversationRequest):
    """Rename a conversation."""
    meta = conv_store.rename_conversation(conv_id, req.title)
    if not meta:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"ok": True, "conversation": meta}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and its messages."""
    if not conv_store.delete_conversation(conv_id):
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"ok": True}


@router.post("/conversations/{conv_id}/load")
async def load_conversation(conv_id: str):
    """Load a saved conversation into the ai_service in-memory history
    so subsequent /chat calls continue from that point."""
    meta = conv_store.get_conversation(conv_id)
    if not meta:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    messages = conv_store.get_messages(conv_id)
    ai = get_ai_service()
    ai.clear_history(conv_id)
    for msg in messages:
        if msg.get("role") in ("user", "assistant", "tool"):
            clean_msg = {"role": msg["role"]}
            if "content" in msg:
                content = msg["content"]
                if msg.get("role") == "assistant":
                    content = strip_dsml_tool_markup(content)
                clean_msg["content"] = content
            if msg.get("role") == "tool":
                clean_msg.update({
                    "tool_call_id": msg.get("tool_call_id"),
                    "toolName": msg.get("toolName"),
                    "toolArgs": msg.get("toolArgs"),
                    "toolSummary": msg.get("toolSummary"),
                    "toolError": msg.get("toolError"),
                })
            ai.append_history(conv_id, clean_msg)
    return {"ok": True, "loaded": len(messages), "conversation": meta}
