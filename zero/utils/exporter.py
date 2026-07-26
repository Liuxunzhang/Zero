"""Export functionality for results"""

import csv
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime

from zero import config

logger = logging.getLogger(__name__)


class ResultExporter:
    """Export table results to various formats"""

    @staticmethod
    def export_csv(columns: List[str], rows: List[Tuple], filepath: str) -> bool:
        """Export results to CSV file"""
        try:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(rows)
            return True
        except Exception as e:
            logger.error("CSV export failed: %s", e, exc_info=True)
            return False

    @staticmethod
    def export_json(columns: List[str], rows: List[Tuple], filepath: str) -> bool:
        """Export results to JSON file"""
        try:
            data = []
            for row in rows:
                # strict=False: a ragged row from a plugin should still export,
                # truncated to the columns it has, rather than raise.
                row_dict = {col: val for col, val in zip(columns, row, strict=False)}
                data.append(row_dict)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error("JSON export failed: %s", e, exc_info=True)
            return False

    @staticmethod
    def export_txt(columns: List[str], rows: List[Tuple], filepath: str) -> bool:
        """Export results to plain text file"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                # Write header
                f.write(" | ".join(columns) + "\n")
                f.write("-" * (sum(len(c) for c in columns) + len(columns) * 3) + "\n")

                # Write rows
                for row in rows:
                    f.write(" | ".join(str(cell) for cell in row) + "\n")
            return True
        except Exception as e:
            logger.error("TXT export failed: %s", e, exc_info=True)
            return False

    @staticmethod
    def auto_export(
        columns: List[str],
        rows: List[Tuple],
        plugin_name: str,
        fmt: str = "csv",
    ) -> Optional[str]:
        """Auto-generate filename and export. Returns the path, or None on failure."""
        writers = {
            "csv": ResultExporter.export_csv,
            "json": ResultExporter.export_json,
            "txt": ResultExporter.export_txt,
        }
        writer = writers.get(fmt)
        if writer is None:
            logger.error("Unsupported export format: %s", fmt)
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{plugin_name}_{timestamp}.{fmt}"

        export_dir = Path(getattr(config, "EXPORT_DIR", "~/zero_exports")).expanduser()
        export_dir.mkdir(parents=True, exist_ok=True)

        filepath = export_dir / filename
        success = writer(columns, rows, str(filepath))
        return str(filepath) if success else None
