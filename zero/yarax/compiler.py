"""Safe rule-tree validation and YARA-X compilation helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ValidationError

RULE_SUFFIXES = {".yar", ".yara"}
MANIFEST_NAME = "zero-yara.json"
_INCLUDE_RE = re.compile(r'(?m)^\s*include\s+"([^"\r\n]+)"')
_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\r\n]*", re.S)
_VALID_GLOBAL_TYPES = {"bool", "boolean", "str", "string", "bytes", "int", "integer", "float"}


def normalize_relative_path(value: str) -> str:
    """Return a canonical package-relative POSIX path or raise."""
    value = str(value or "").replace("\\", "/")
    path = PurePosixPath(value)
    if not value or value.startswith("/") or path.is_absolute():
        raise ValidationError(f"Absolute or empty package path is not allowed: {value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationError(f"Unsafe package path: {value!r}")
    if "\x00" in value:
        raise ValidationError("NUL is not allowed in package paths")
    return path.as_posix()


def load_manifest(root: Path, override: dict[str, Any] | None = None) -> dict[str, Any]:
    path = root / MANIFEST_NAME
    manifest: dict[str, Any] = {}
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Invalid {MANIFEST_NAME}: {exc}")
    if override:
        manifest.update(override)
    manifest.setdefault("schema_version", 1)
    manifest.setdefault("name", root.name)
    manifest.setdefault("version", "1.0.0")
    manifest.setdefault("description", "")
    manifest.setdefault("license", "")
    manifest.setdefault("homepage", "")
    manifest.setdefault("globals", {})
    if manifest["schema_version"] != 1:
        raise ValidationError("manifest.schema_version must be 1")
    for key in ("name", "version", "description", "license", "homepage"):
        if not isinstance(manifest.get(key), str):
            raise ValidationError(f"manifest.{key} must be a string")
    if not isinstance(manifest.get("entrypoints"), (list, type(None))):
        raise ValidationError("manifest.entrypoints must be a list")
    if manifest.get("entrypoints") is not None and not all(
        isinstance(item, str) for item in manifest["entrypoints"]
    ):
        raise ValidationError("manifest.entrypoints items must be strings")
    if not isinstance(manifest["globals"], dict):
        raise ValidationError("manifest.globals must be an object")
    return manifest


def discover_files(root: Path) -> list[str]:
    result: list[str] = []
    casefold_seen: dict[str, str] = {}
    inode_seen: set[tuple[int, int]] = set()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValidationError(f"Symbolic links are not allowed: {rel}")
        if path.is_dir():
            continue
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValidationError(f"Could not inspect {rel}: {exc}")
        if not path.is_file():
            raise ValidationError(f"Special files are not allowed: {rel}")
        inode = (stat.st_dev, stat.st_ino)
        if stat.st_nlink > 1 or inode in inode_seen:
            raise ValidationError(f"Hard links are not allowed: {rel}")
        inode_seen.add(inode)
        folded = rel.casefold()
        if folded in casefold_seen and casefold_seen[folded] != rel:
            raise ValidationError(
                f"Case-conflicting paths: {casefold_seen[folded]!r} and {rel!r}"
            )
        casefold_seen[folded] = rel
        result.append(rel)
    return result


def extract_includes(text: str) -> list[str]:
    return _INCLUDE_RE.findall(_COMMENT_RE.sub("", text))


def build_include_graph(root: Path, rule_files: list[str]) -> dict[str, list[str]]:
    available = set(rule_files)
    graph: dict[str, list[str]] = {name: [] for name in rule_files}
    for source in rule_files:
        try:
            text = (root / source).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValidationError(f"Could not read YARA file {source}: {exc}")
        for raw in extract_includes(text):
            include = normalize_relative_path(raw)
            # YARA-X include dirs resolve from package root. Deliberately do not
            # resolve relative to the including file, which could be ambiguous.
            if include not in available:
                raise ValidationError(f"Missing include {include!r} referenced by {source!r}")
            graph[source].append(include)

    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        if state.get(node) == 1:
            start = stack.index(node) if node in stack else 0
            raise ValidationError("Cyclic include: " + " -> ".join(stack[start:] + [node]))
        if state.get(node) == 2:
            return
        state[node] = 1
        stack.append(node)
        for child in graph.get(node, []):
            visit(child)
        stack.pop()
        state[node] = 2

    for item in rule_files:
        visit(item)
    return graph


def infer_entrypoints(rule_files: list[str], graph: dict[str, list[str]]) -> list[str]:
    included = {item for children in graph.values() for item in children}
    inferred = [item for item in rule_files if item not in included]
    return inferred or list(rule_files)


def normalize_globals(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    values: dict[str, Any] = {}
    specs: dict[str, dict[str, Any]] = {}
    for name, item in raw.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
            raise ValidationError(f"Invalid global identifier: {name!r}")
        if isinstance(item, dict) and ("type" in item or "default" in item):
            typ = str(item.get("type") or "").lower()
            value = item.get("default")
        else:
            value = item
            if isinstance(value, bool):
                typ = "bool"
            elif isinstance(value, int):
                typ = "int"
            elif isinstance(value, float):
                typ = "float"
            elif isinstance(value, str):
                typ = "string"
            else:
                raise ValidationError(f"Global {name!r} has unsupported value type")
        if typ not in _VALID_GLOBAL_TYPES:
            raise ValidationError(f"Global {name!r} has unsupported type {typ!r}")
        canonical = {
            "boolean": "bool", "str": "string", "integer": "int"
        }.get(typ, typ)
        if canonical == "bool" and not isinstance(value, bool):
            raise ValidationError(f"Global {name!r} default must be boolean")
        if canonical == "int" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValidationError(f"Global {name!r} default must be integer")
        if canonical == "float" and not isinstance(value, (int, float)):
            raise ValidationError(f"Global {name!r} default must be numeric")
        if canonical == "string" and not isinstance(value, str):
            raise ValidationError(f"Global {name!r} default must be string")
        if canonical == "bytes":
            if not isinstance(value, str):
                raise ValidationError(f"Global {name!r} bytes default must be a UTF-8 string")
            compiled_value = value.encode("utf-8")
        else:
            compiled_value = value
        values[str(name)] = compiled_value
        specs[str(name)] = {"type": canonical, "default": value}
    return values, specs


def inspect_package(root: Path, manifest_override: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    files = discover_files(root)
    rule_files = [name for name in files if Path(name).suffix.lower() in RULE_SUFFIXES]
    if not rule_files:
        raise ValidationError("The package does not contain any .yar or .yara files")
    graph = build_include_graph(root, rule_files)
    manifest = load_manifest(root, manifest_override)
    entrypoints = manifest.get("entrypoints") or infer_entrypoints(rule_files, graph)
    entrypoints = [normalize_relative_path(item) for item in entrypoints]
    for entrypoint in entrypoints:
        if entrypoint not in rule_files:
            raise ValidationError(f"Entrypoint is not a YARA file: {entrypoint!r}")
    _, global_specs = normalize_globals(manifest.get("globals") or {})
    manifest["entrypoints"] = entrypoints
    manifest["globals"] = global_specs
    digest = package_digest(root, files, manifest)
    return {
        "manifest": manifest,
        "files": files,
        "rule_files": rule_files,
        "entrypoints": entrypoints,
        "include_graph": graph,
        "content_digest": digest,
    }


def package_digest(root: Path, files: list[str], manifest: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    for name in sorted(files):
        if name == MANIFEST_NAME:
            continue
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        with (root / name).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _diagnostics(
    compiler, exception: Exception | None = None, package_root: Path | None = None
) -> list[dict[str, Any]]:
    raw_errors = []
    try:
        raw_errors = list(compiler.errors())
    except Exception:
        pass
    if not raw_errors and exception is not None:
        return [{
            "severity": "error", "code": "compile_error", "message": str(exception),
            "file": None, "line": None, "column": None, "span": None,
        }]
    result: list[dict[str, Any]] = []
    for item in raw_errors:
        labels = item.get("labels") or [{}]
        label = labels[0]
        span = label.get("span")
        origin = label.get("code_origin")
        if origin and package_root:
            try:
                origin_path = Path(origin)
                if origin_path.is_absolute():
                    origin = origin_path.resolve().relative_to(package_root.resolve()).as_posix()
            except (OSError, ValueError):
                origin = Path(str(origin)).name
        result.append({
            "severity": label.get("level") or "error",
            "code": item.get("code") or item.get("type") or "compile_error",
            "message": label.get("text") or item.get("title") or item.get("text") or "",
            "file": origin,
            "line": label.get("line") or item.get("line"),
            "column": label.get("column") or item.get("column"),
            "span": span,
        })
    return result


def compile_package(
    root: Path,
    manifest: dict[str, Any] | None = None,
    *,
    relaxed_regex: bool = False,
    output_path: Path | None = None,
):
    """Validate and compile a package, optionally serializing immutable rules."""
    info = inspect_package(root, manifest)
    try:
        import yara_x
    except ImportError as exc:
        raise ValidationError("yara-x==1.19.0 is not installed") from exc
    compiler = yara_x.Compiler(
        relaxed_re_syntax=bool(relaxed_regex),
        includes_enabled=True,
    )
    compiler.add_include_dir(str(Path(root).resolve()))
    global_values, _ = normalize_globals(info["manifest"].get("globals") or {})
    for name, value in global_values.items():
        compiler.define_global(name, value)
    error: Exception | None = None
    for entrypoint in info["entrypoints"]:
        try:
            source = (Path(root) / entrypoint).read_text(encoding="utf-8")
            compiler.add_source(source, origin=entrypoint)
        except Exception as exc:
            error = exc
    diagnostics = _diagnostics(compiler, error, Path(root))
    if diagnostics:
        raise ValidationError("YARA-X compilation failed", diagnostics)
    try:
        rules = compiler.build()
    except Exception as exc:
        raise ValidationError(
            "YARA-X compilation failed", _diagnostics(compiler, exc, Path(root))
        )
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = output_path.with_name(output_path.name + f".tmp-{os.getpid()}")
        with tmp.open("wb") as handle:
            rules.serialize_into(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, output_path)
    return rules, info
