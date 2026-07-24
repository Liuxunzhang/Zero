"""Disk-backed result cache helpers for Volatility plugin outputs."""

from __future__ import annotations

import csv
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

from zero.core.cache_key import (
    disk_image_dir,
    disk_plugin_dir,
    disk_result_path,
    image_identity,
    kwargs_digest,
    normalize_kwargs,
    safe_name,
)

logger = logging.getLogger(__name__)


class DiskResultCache:
    """Manage loading/saving cached plugin results on disk.

    Layout::

        {results_root}/{image_id}/{plugin_safe}/{kwargs_digest}.csv

    Old layout ``{image_name}/{plugin}.csv`` is intentionally not read
    (see optimization plan: stale caches are discarded).
    """

    def __init__(self, results_root: Path, enabled: bool = True, fmt: str = "csv") -> None:
        self.results_root = Path(results_root)
        self.enabled = bool(enabled)
        self.fmt = str(fmt or "csv").lower()
        self.results_root.mkdir(parents=True, exist_ok=True)

    # Back-compat alias used by tests / callers.
    _safe_name = staticmethod(safe_name)

    def _result_file_path(
        self,
        image_path: str,
        plugin_name: str,
        kwargs: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Path]:
        if not image_path or self.fmt != "csv":
            return None
        return disk_result_path(
            self.results_root,
            image_path,
            plugin_name,
            kwargs=kwargs,
            fmt=self.fmt,
        )

    def load(
        self,
        image_path: str,
        plugin_name: str,
        kwargs: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Tuple[List[str], List[Tuple]]]:
        if not self.enabled:
            return None
        result_path = self._result_file_path(image_path, plugin_name, kwargs)
        if not result_path or not result_path.exists():
            return None

        try:
            with result_path.open("r", newline="", encoding="utf-8") as f:
                rows_list = list(csv.reader(f))
            if not rows_list:
                return None
            columns = rows_list[0]
            data_rows = [tuple(row) for row in rows_list[1:]]
            return columns, data_rows
        except Exception as e:
            logger.error("Failed to load cached file %s: %s", result_path, e, exc_info=True)
            return None

    def save(
        self,
        image_path: str,
        plugin_name: str,
        columns: List[str],
        rows: List[Tuple],
        kwargs: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        if not self.enabled:
            return False
        result_path = self._result_file_path(image_path, plugin_name, kwargs)
        if not result_path:
            return False

        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            with result_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(rows)
            meta_path = result_path.with_suffix(".meta.json")
            meta = {
                "plugin": plugin_name,
                "kwargs": normalize_kwargs(kwargs),
                "kwargs_digest": kwargs_digest(kwargs),
                "row_count": len(rows),
                "columns": list(columns),
                "image_path": image_path,
                "image_id": image_identity(image_path),
                "created_at": time.time(),
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            logger.error("Failed to save cached file %s: %s", result_path, e, exc_info=True)
            return False

    def delete(
        self,
        image_path: str,
        plugin_name: str,
        kwargs: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Delete one kwargs variant, or all variants for a plugin if kwargs is None.

        Returns number of files removed.
        """
        if not image_path or not plugin_name:
            return 0
        removed = 0
        if kwargs is not None:
            path = self._result_file_path(image_path, plugin_name, kwargs)
            if path and path.exists():
                try:
                    path.unlink()
                    removed += 1
                except OSError as e:
                    logger.error("Failed to delete cache file %s: %s", path, e)
            meta = path.with_suffix(".meta.json") if path else None
            if meta and meta.exists():
                try:
                    meta.unlink()
                except OSError:
                    pass
            # Remove empty plugin dir
            plugin_dir = disk_plugin_dir(self.results_root, image_path, plugin_name)
            self._rmdir_if_empty(plugin_dir)
            return removed

        plugin_dir = disk_plugin_dir(self.results_root, image_path, plugin_name)
        if plugin_dir.is_dir():
            for f in plugin_dir.glob(f"*.{self.fmt}"):
                try:
                    f.unlink()
                    removed += 1
                except OSError as e:
                    logger.error("Failed to delete cache file %s: %s", f, e)
            for f in plugin_dir.glob("*.meta.json"):
                try:
                    f.unlink()
                except OSError:
                    pass
            self._rmdir_if_empty(plugin_dir)
        return removed

    def clear_image(self, image_path: str) -> int:
        """Remove all cached results for one image. Returns files removed."""
        if not image_path:
            return 0
        image_dir = disk_image_dir(self.results_root, image_path)
        if not image_dir.is_dir():
            return 0
        removed = 0
        try:
            for f in image_dir.rglob(f"*.{self.fmt}"):
                if f.is_file():
                    removed += 1
            shutil.rmtree(image_dir, ignore_errors=True)
        except OSError as e:
            logger.error("Failed to clear image cache %s: %s", image_dir, e)
        return removed

    def clear_all(self) -> int:
        """Remove entire cache root contents. Returns approximate file count."""
        if not self.results_root.is_dir():
            return 0
        removed = 0
        try:
            for f in self.results_root.rglob(f"*.{self.fmt}"):
                if f.is_file():
                    removed += 1
            for child in list(self.results_root.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                elif child.is_file():
                    try:
                        child.unlink()
                    except OSError:
                        pass
        except OSError as e:
            logger.error("Failed to clear all cache under %s: %s", self.results_root, e)
        return removed

    def count_files(self, image_path: Optional[str] = None) -> int:
        """Count cached result files, optionally scoped to one image."""
        root = disk_image_dir(self.results_root, image_path) if image_path else self.results_root
        if not root.is_dir():
            return 0
        return sum(1 for f in root.rglob(f"*.{self.fmt}") if f.is_file())

    @staticmethod
    def _rmdir_if_empty(path: Path) -> None:
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                parent = path.parent
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
        except OSError:
            pass
