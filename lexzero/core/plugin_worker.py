"""Standalone plugin worker process for macOS-safe execution."""

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any


def _emit(event_type: str, payload) -> None:
    print(json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False), flush=True)


def _default_dump_dir() -> str:
    """Return project-root/dumps/vol3 as the fallback dump output directory."""
    return str(Path(__file__).resolve().parents[2] / "dumps" / "vol3")


def _make_file_handler(dump_dir: str):
    """Return a concrete FileHandlerInterface subclass that writes to dump_dir."""
    from volatility3.framework import interfaces

    os.makedirs(dump_dir, exist_ok=True)

    class _FileHandler(io.BytesIO, interfaces.plugins.FileHandlerInterface):
        def __init__(self, filename: str) -> None:
            io.BytesIO.__init__(self)
            interfaces.plugins.FileHandlerInterface.__init__(self, filename)

        def close(self) -> None:
            if self.closed:
                return
            self.seek(0)
            data = self.read()
            base_path = os.path.join(dump_dir, self.preferred_filename)
            name, ext = os.path.splitext(base_path)
            output_path = base_path
            counter = 1
            while os.path.exists(output_path):
                output_path = f"{name}-{counter}{ext}"
                counter += 1
            with open(output_path, "wb") as fh:
                fh.write(data)
            super().close()

    return _FileHandler


def _apply_plugin_kwargs(
    ctx,
    plugin_class,
    base_config_path: str,
    plugin_kwargs: Dict[str, Any],
) -> None:
    """Set user-supplied kwargs as vol3 context config values."""
    from volatility3.framework.configuration import requirements as reqs

    req_map = {r.name: r for r in plugin_class.get_requirements()}
    class_name = plugin_class.__name__

    for key, raw_value in plugin_kwargs.items():
        if key == "dump_dir":
            continue  # handled via FileHandlerInterface, not ctx.config
        if raw_value is None or raw_value == "":
            continue

        config_path = f"{base_config_path}.{class_name}.{key}"
        req = req_map.get(key)

        try:
            if isinstance(req, reqs.IntRequirement):
                v = raw_value
                if isinstance(v, str):
                    v = int(v, 16) if v.strip().startswith(("0x", "0X")) else int(v)
                ctx.config[config_path] = int(v)
            elif isinstance(req, reqs.ListRequirement):
                if isinstance(raw_value, list):
                    items = raw_value
                elif isinstance(raw_value, str):
                    items = [s.strip() for s in raw_value.split(",") if s.strip()]
                else:
                    items = [raw_value]
                element_type = getattr(req, "element_type", None)
                is_int_list = element_type is not None and hasattr(element_type, "__mro__") and any(
                    "Int" in c.__name__ for c in element_type.__mro__
                )
                if is_int_list:
                    coerced = []
                    for item in items:
                        s = str(item).strip()
                        coerced.append(int(s, 16) if s.startswith(("0x", "0X")) else int(s))
                    ctx.config[config_path] = coerced
                else:
                    ctx.config[config_path] = items
            elif isinstance(req, reqs.BooleanRequirement):
                if isinstance(raw_value, bool):
                    ctx.config[config_path] = raw_value
                else:
                    ctx.config[config_path] = str(raw_value).lower() in ("true", "1", "yes", "on")
            elif isinstance(req, reqs.StringRequirement):
                ctx.config[config_path] = str(raw_value)
            else:
                # Unknown or non-standard requirement — pass through as-is.
                ctx.config[config_path] = raw_value
        except Exception:
            # Never abort on a bad kwarg; vol3 will surface the error later.
            pass


def _run_plugin(
    image_path: str,
    symbol_dirs: List[str],
    plugin_name: str,
    plugin_kwargs: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[Tuple[str, ...]]]:
    import volatility3.plugins
    import volatility3.symbols
    from volatility3 import framework
    from volatility3.framework import contexts, automagic, plugins as framework_plugins
    from volatility3.framework.automagic import stacker
    from volatility3.framework.configuration import requirements
    from volatility3.framework import constants as vol_constants

    plugin_kwargs = plugin_kwargs or {}

    framework.require_interface_version(2, 0, 0)
    failures = framework.import_files(volatility3.plugins, True)
    if failures:
        _emit("progress", f"Plugin import failures: {len(failures)}")

    volatility3.symbols.__path__ = [
        str(Path(p).resolve()) for p in symbol_dirs
    ] + vol_constants.SYMBOL_BASEPATHS

    ctx = contexts.Context()
    plugin_map = framework.list_plugins()
    if plugin_name not in plugin_map:
        raise ValueError(f"Unknown plugin: {plugin_name}")

    plugin_class = plugin_map[plugin_name]
    base_config_path = "plugins"
    ctx.config["automagic.LayerStacker.single_location"] = (
        requirements.URIRequirement.location_from_file(image_path)
    )

    _apply_plugin_kwargs(ctx, plugin_class, base_config_path, plugin_kwargs)

    automagics = automagic.available(ctx)
    automagics = automagic.choose_automagic(automagics, plugin_class)
    if ctx.config.get("automagic.LayerStacker.stackers", None) is None:
        ctx.config["automagic.LayerStacker.stackers"] = stacker.choose_os_stackers(plugin_class)

    def framework_progress(progress: float, description: Optional[str] = None):
        msg = f"Progress: {float(progress):.2f} {description or ''}".strip()
        _emit("progress", msg)

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

    grid.populate(visitor, None)
    return columns, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero plugin worker")
    parser.add_argument("--image", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--kwargs", default="{}", help="JSON-encoded plugin kwargs")
    args = parser.parse_args()

    try:
        symbol_dirs = json.loads(args.symbols)
        plugin_kwargs = json.loads(args.kwargs) if args.kwargs else {}
        columns, rows = _run_plugin(args.image, symbol_dirs, args.plugin, plugin_kwargs)
        _emit("result", {"columns": columns, "rows": rows})
        return 0
    except Exception as exc:
        import traceback as _tb
        message = str(exc)
        try:
            from volatility3.framework import exceptions as _vol_exc
            if isinstance(exc, _vol_exc.UnsatisfiedException) and hasattr(exc, "unsatisfied"):
                # unsatisfied 是 {req_path: RequirementInterface} 的字典
                missing = list(exc.unsatisfied.keys())
                # 只保留最后一段（去掉 "plugins.PluginName." 前缀），更易读
                short = [r.rsplit(".", 1)[-1] for r in missing]
                message = (
                    f"此插件需要以下必填参数，无法直接运行: {', '.join(short)}"
                    f"（原始路径: {', '.join(missing)}）"
                )
        except Exception:
            pass
        _emit("error", {"message": message, "traceback": _tb.format_exc()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
