"""Disk-backed result cache helpers for Volatility plugin outputs."""

import csv
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple


class DiskResultCache:
    """Manage loading/saving cached plugin results on disk."""

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

    def _result_file_path(self, image_path: str, plugin_name: str) -> Optional[Path]:
        if not image_path or self.fmt != "csv":
            return None
        image_name = self._safe_name(Path(image_path).name)
        plugin_name_safe = self._safe_name(plugin_name)
        image_dir = self.results_root / image_name
        image_dir.mkdir(parents=True, exist_ok=True)
        return image_dir / f"{plugin_name_safe}.csv"

    def load(self, image_path: str, plugin_name: str) -> Optional[Tuple[List[str], List[Tuple]]]:
        if not self.enabled:
            return None
        result_path = self._result_file_path(image_path, plugin_name)
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

    def save(self, image_path: str, plugin_name: str, columns: List[str], rows: List[Tuple]) -> bool:
        if not self.enabled:
            return False
        result_path = self._result_file_path(image_path, plugin_name)
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
