"""Disk-backed result cache helpers for Volatility plugin outputs."""

import csv
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class DiskResultCache:
    """Manage loading/saving cached plugin results on disk.

    Cache keys incorporate the plugin's ``kwargs`` (pid / offset / key / …) so
    that ``handles.Handles pid=4376`` and ``handles.Handles pid=100`` do not
    collide on the same on-disk CSV.  The kwargs are folded into a short
    signature hash appended to the filename.
    """

    def __init__(self, results_root: Path, enabled: bool = True, fmt: str = "csv") -> None:
        self.results_root = results_root
        self.enabled = bool(enabled)
        self.fmt = str(fmt or "csv").lower()
        self.results_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = (value or "").strip() or "unknown"
        cleaned = cleaned.replace("/", "_").replace("\\", "_")
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", cleaned)
        return cleaned[:180]

    @staticmethod
    def _kwargs_signature(kwargs: Optional[Dict[str, Any]]) -> str:
        """Return a short, stable hash for the plugin kwargs.

        ``None`` or empty kwargs return the empty string so that
        parameter-less plugins keep their plain ``{plugin}.csv`` filename
        (backwards-compatible with previously-generated cache files).
        """
        if not kwargs:
            return ""
        try:
            canonical = json.dumps(
                kwargs, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
        except (TypeError, ValueError):
            # Unserialisable kwargs (rare) → fall back to a string hash so
            # two different inputs still diverge.
            return hashlib.sha1(repr(sorted(kwargs.items())).encode("utf-8")).hexdigest()[:12]

    def _result_file_path(
        self,
        image_path: str,
        plugin_name: str,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Optional[Path]:
        if not image_path or self.fmt != "csv":
            return None
        image_name = self._safe_name(Path(image_path).name)
        plugin_name_safe = self._safe_name(plugin_name)
        sig = self._kwargs_signature(kwargs)
        filename = f"{plugin_name_safe}__{sig}.csv" if sig else f"{plugin_name_safe}.csv"
        image_dir = self.results_root / image_name
        image_dir.mkdir(parents=True, exist_ok=True)
        return image_dir / filename

    def load(
        self,
        image_path: str,
        plugin_name: str,
        kwargs: Optional[Dict[str, Any]] = None,
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
            logging.error("Failed to load cached file %s: %s", result_path, e, exc_info=True)
            return None

    def save(
        self,
        image_path: str,
        plugin_name: str,
        columns: List[str],
        rows: List[Tuple],
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self.enabled:
            return False
        result_path = self._result_file_path(image_path, plugin_name, kwargs)
        if not result_path:
            return False

        try:
            with result_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(rows)
            return True
        except Exception as e:
            logging.error("Failed to save cached file %s: %s", result_path, e, exc_info=True)
            return False

    def delete(self, image_path: str, plugin_name: str) -> int:
        """Delete ALL kwargs variants of a plugin's cache for the given image.

        Returns the number of files removed.  Used by ``clear_cache`` so that
        purging ``handles.Handles`` wipes every pid/offset variant on disk.
        """
        if not image_path:
            return 0
        image_name = self._safe_name(Path(image_path).name)
        plugin_name_safe = self._safe_name(plugin_name)
        image_dir = self.results_root / image_name
        if not image_dir.exists():
            return 0
        removed = 0
        # Match both the bare ``{plugin}.csv`` and any ``{plugin}__{sig}.csv``.
        for candidate in image_dir.glob(f"{plugin_name_safe}.csv"):
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                pass
        for candidate in image_dir.glob(f"{plugin_name_safe}__*.csv"):
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def delete_all(self, image_path: str) -> int:
        """Delete every cached CSV for the given image.  Returns the count."""
        if not image_path:
            return 0
        image_name = self._safe_name(Path(image_path).name)
        image_dir = self.results_root / image_name
        if not image_dir.exists():
            return 0
        removed = 0
        for candidate in image_dir.glob("*.csv"):
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                pass
        return removed
