#!/usr/bin/env python3
"""Clear Zero plugin result disk cache under saved_results/vol3/.

Examples
--------
  # Preview everything that would be removed
  python scripts/clear_stale_cache.py --all --dry-run

  # Delete cache older than 30 days
  python scripts/clear_stale_cache.py --older-than-days 30

  # Delete one image_id directory (folder name under saved_results/vol3/)
  python scripts/clear_stale_cache.py --image-id dump.raw_ab12cd34ef567890
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _cache_root() -> Path:
    return _project_root() / "saved_results" / "vol3"


def _entry_age_seconds(path: Path) -> float:
    """Prefer meta created_at; fall back to file mtime."""
    meta = path.with_suffix(".meta.json") if path.suffix == ".csv" else path.parent / f"{path.stem}.meta.json"
    # disk layout: digest.csv + digest.meta.json
    if path.suffix == ".csv":
        meta = path.with_name(path.stem + ".meta.json")
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            created = float(data.get("created_at") or 0)
            if created > 0:
                return max(0.0, time.time() - created)
        except Exception:
            pass
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


def _iter_csv_files(root: Path, image_id: str | None):
    base = root / image_id if image_id else root
    if not base.is_dir():
        return
    yield from (p for p in base.rglob("*.csv") if p.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clear Zero disk result cache")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Cache root (default: <project>/saved_results/vol3)",
    )
    parser.add_argument("--all", action="store_true", help="Delete entire cache tree under root")
    parser.add_argument("--image-id", type=str, default=None, help="Only this image_id subdirectory")
    parser.add_argument(
        "--older-than-days",
        type=float,
        default=None,
        help="Only files older than N days (by meta created_at or mtime)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    args = parser.parse_args(argv)

    root = (args.root or _cache_root()).expanduser().resolve()
    if not root.exists():
        print(f"Cache root does not exist: {root}")
        return 0

    if not args.all and args.image_id is None and args.older_than_days is None:
        parser.error("Specify --all, --image-id, and/or --older-than-days")

    max_age = None
    if args.older_than_days is not None:
        max_age = float(args.older_than_days) * 86400.0

    removed_files = 0
    removed_bytes = 0

    if args.all and args.older_than_days is None and args.image_id is None:
        # Wipe whole tree
        for child in list(root.iterdir()):
            print(f"{'DRY ' if args.dry_run else ''}REMOVE {child}")
            if not args.dry_run:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
        print("Done (full clear).")
        return 0

    targets: list[Path] = []
    for csv_path in _iter_csv_files(root, args.image_id):
        if max_age is not None and _entry_age_seconds(csv_path) < max_age:
            continue
        targets.append(csv_path)

    for csv_path in targets:
        meta = csv_path.with_name(csv_path.stem + ".meta.json")
        for p in (csv_path, meta):
            if not p.exists():
                continue
            size = p.stat().st_size if p.is_file() else 0
            print(f"{'DRY ' if args.dry_run else ''}DELETE {p} ({size} bytes)")
            if not args.dry_run:
                try:
                    p.unlink()
                    removed_files += 1
                    removed_bytes += size
                except OSError as e:
                    print(f"  failed: {e}", file=sys.stderr)
        # prune empty plugin / image dirs
        if not args.dry_run:
            for parent in (csv_path.parent, csv_path.parent.parent):
                try:
                    if parent.is_dir() and parent != root and not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass

    print(
        f"Done. files={'dry-run' if args.dry_run else removed_files}, "
        f"bytes≈{removed_bytes}, candidates={len(targets)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
