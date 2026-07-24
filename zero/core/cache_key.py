"""Stable cache keys for plugin results (image + plugin + kwargs)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

# Fixed digest for empty / no-param runs (readable on disk).
NOPARAMS_DIGEST = "noparams"

_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def safe_name(value: str, max_len: int = 180) -> str:
    """Filesystem-safe fragment for directory/file names."""
    cleaned = (value or "").strip() or "unknown"
    cleaned = cleaned.replace("/", "_").replace("\\", "_")
    cleaned = _SAFE_RE.sub("_", cleaned)
    return cleaned[:max_len]


def normalize_kwargs(kwargs: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Drop empty values and coerce to JSON-stable primitives."""
    if not kwargs:
        return {}

    def _coerce(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            return value
        if isinstance(value, str):
            text = value.strip()
            return text if text else None
        if isinstance(value, Mapping):
            nested = normalize_kwargs(value)
            return nested or None
        if isinstance(value, (list, tuple)):
            items = []
            for item in value:
                coerced = _coerce(item)
                if coerced is not None and coerced != "":
                    items.append(coerced)
            return items or None
        # Non-JSON-friendly types: stable string form.
        text = str(value).strip()
        return text if text else None

    out: Dict[str, Any] = {}
    for raw_key, raw_val in kwargs.items():
        key = str(raw_key)
        coerced = _coerce(raw_val)
        if coerced is None or coerced == "":
            continue
        out[key] = coerced
    return out


def kwargs_digest(kwargs: Optional[Mapping[str, Any]]) -> str:
    """Short digest of normalized kwargs; empty -> noparams."""
    normalized = normalize_kwargs(kwargs)
    if not normalized:
        return NOPARAMS_DIGEST
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def image_identity(image_path: str) -> str:
    """Stable short id for a memory image file.

    Uses resolved path + mtime_ns + size so same basename with different
    content does not collide. Falls back to basename when stat fails.
    """
    if not image_path:
        return "unknown"
    try:
        path = Path(image_path).expanduser().resolve()
    except OSError:
        path = Path(image_path)
    try:
        st = path.stat()
        material = f"{path}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        material = f"{path}|missing"
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
    base = safe_name(path.name, max_len=80)
    return f"{base}_{digest}"


def make_cache_key(
    image_path: str,
    plugin_name: str,
    kwargs: Optional[Mapping[str, Any]] = None,
) -> str:
    """In-memory cache key: image_id:plugin:kwargs_digest."""
    image_id = image_identity(image_path)
    plugin = (plugin_name or "").strip() or "unknown"
    digest = kwargs_digest(kwargs)
    return f"{image_id}:{plugin}:{digest}"


def plugin_key_prefix(image_path: str, plugin_name: str) -> str:
    """Prefix matching all kwargs variants for a plugin on an image."""
    image_id = image_identity(image_path)
    plugin = (plugin_name or "").strip() or "unknown"
    return f"{image_id}:{plugin}:"


def disk_result_path(
    results_root: Path,
    image_path: str,
    plugin_name: str,
    kwargs: Optional[Mapping[str, Any]] = None,
    fmt: str = "csv",
) -> Path:
    """Path: {root}/{image_id}/{plugin_safe}/{digest}.{fmt}."""
    image_id = safe_name(image_identity(image_path))
    plugin_safe = safe_name(plugin_name)
    digest = kwargs_digest(kwargs)
    ext = (fmt or "csv").lower().lstrip(".")
    return results_root / image_id / plugin_safe / f"{digest}.{ext}"


def disk_plugin_dir(
    results_root: Path,
    image_path: str,
    plugin_name: str,
) -> Path:
    """Directory holding all kwargs variants for a plugin."""
    image_id = safe_name(image_identity(image_path))
    plugin_safe = safe_name(plugin_name)
    return results_root / image_id / plugin_safe


def disk_image_dir(results_root: Path, image_path: str) -> Path:
    """Directory for all cached plugins of an image."""
    image_id = safe_name(image_identity(image_path))
    return results_root / image_id
