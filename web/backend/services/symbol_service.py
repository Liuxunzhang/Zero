"""Symbol table service for remote browsing and local download management."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import requests

from zero import config

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REPO_OWNER = "Liuxunzhang"
_REPO_NAME = "volatility3-symbols"
_API_BASE = "https://api.github.com"
_RAW_BASE = f"https://raw.githubusercontent.com/{_REPO_OWNER}/{_REPO_NAME}"
_INDEX_TTL_SECONDS = 600
_LOCAL_INDEX_TTL_SECONDS = 10
_ALLOWED_SUFFIXES = (".json", ".json.xz", ".json.gz", ".zip")


def _resolve_symbol_root() -> Path:
    configured = str(getattr(config, "SYMBOL_TABLE_RELATIVE_PATH", "symbols") or "symbols").strip()
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return path


class SymbolService:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Zero-SymbolManager/1.0"})
        self._cache_expires_at: float = 0.0
        self._default_branch: str = "main"
        self._remote_index: list[dict] = []
        self._last_remote_error: str = ""
        self._last_remote_updated_at: int = 0
        self._local_cache_expires_at: float = 0.0
        self._local_cache_payload: Optional[dict] = None

    def _fetch_default_branch(self) -> str:
        url = f"{_API_BASE}/repos/{_REPO_OWNER}/{_REPO_NAME}"
        resp = self._session.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
        return str(data.get("default_branch") or "main")

    def _build_remote_index(self) -> list[dict]:
        branch = self._fetch_default_branch()
        self._default_branch = branch

        # Try fast path: recursive git tree API
        try:
            items = self._build_index_via_tree(branch)
            if items is not None:
                return items
        except Exception as e:
            logger.warning("Git tree API failed (%s), falling back to Contents API", e)

        # Fallback: walk via Contents API (handles repos too large for the tree endpoint)
        logger.info("Using Contents API to build symbol index (this may be slow)")
        return self._build_index_via_contents(branch)

    def _build_index_via_tree(self, branch: str) -> list[dict] | None:
        """Try GitHub git/trees?recursive=1. Returns None if truncated or on error."""
        encoded_branch = requests.utils.quote(branch, safe="")
        tree_url = (
            f"{_API_BASE}/repos/{_REPO_OWNER}/{_REPO_NAME}"
            f"/git/trees/{encoded_branch}?recursive=1"
        )
        resp = self._session.get(tree_url, timeout=30)
        resp.raise_for_status()
        data = resp.json() or {}
        if data.get("truncated"):
            logger.warning("Git tree response truncated; switching to Contents API")
            return None

        items: list[dict] = []
        for node in (data.get("tree") or []):
            if node.get("type") != "blob":
                continue
            rel_path = str(node.get("path") or "")
            if not rel_path or not rel_path.endswith(_ALLOWED_SUFFIXES):
                continue
            items.append(self._make_item(rel_path, int(node.get("size") or 0)))

        items.sort(key=lambda x: x["path"])
        return items

    def _build_index_via_contents(self, branch: str, path: str = "") -> list[dict]:
        """Recursively walk the repo via Contents API. Slower but handles huge repos."""
        encoded_branch = requests.utils.quote(branch, safe="")
        url = (
            f"{_API_BASE}/repos/{_REPO_OWNER}/{_REPO_NAME}/contents/{path}"
            f"?ref={encoded_branch}"
        )
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        entries = resp.json() or []

        items: list[dict] = []
        for entry in entries:
            entry_type = entry.get("type")
            entry_path = entry.get("path") or ""
            if entry_type == "dir":
                # Only recurse into directories likely to contain symbol files
                items.extend(self._build_index_via_contents(branch, entry_path))
            elif entry_type == "file":
                if entry_path.endswith(_ALLOWED_SUFFIXES):
                    items.append(self._make_item(entry_path, int(entry.get("size") or 0)))
        return items

    @staticmethod
    def _make_item(rel_path: str, size: int) -> dict:
        name = rel_path.rsplit("/", 1)[-1]
        os_hint = "windows" if "windows" in rel_path.lower() else (
            "linux" if "linux" in rel_path.lower() else "unknown"
        )
        return {"path": rel_path, "name": name, "size": size, "os": os_hint}

    def _ensure_remote_index(self) -> None:
        now = time.time()
        if self._remote_index and now < self._cache_expires_at:
            return
        try:
            self._remote_index = self._build_remote_index()
            self._cache_expires_at = now + _INDEX_TTL_SECONDS
            self._last_remote_error = ""
            self._last_remote_updated_at = int(now)
        except Exception as e:
            self._last_remote_error = str(e)
            logger.warning("Failed to refresh remote symbol index: %s", e)
            if not self._remote_index:
                self._cache_expires_at = now + 30

    def list_remote_symbols(
        self,
        query: str = "",
        os_family: str = "",
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        self._ensure_remote_index()
        q = (query or "").strip().lower()
        os_filter = (os_family or "").strip().lower()

        filtered = self._remote_index
        if q:
            filtered = [
                item for item in filtered
                if q in item["name"].lower() or q in item["path"].lower()
            ]
        if os_filter in {"windows", "linux"}:
            filtered = [item for item in filtered if item["os"] == os_filter]

        total = len(filtered)
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        start = (page - 1) * page_size
        end = start + page_size
        rows = filtered[start:end]

        return {
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "default_branch": self._default_branch,
            "repo": f"{_REPO_OWNER}/{_REPO_NAME}",
            "status": {
                "ok": not bool(self._last_remote_error),
                "error": self._last_remote_error,
                "last_updated_at": self._last_remote_updated_at,
                "cached": bool(self._remote_index),
            },
        }

    def list_local_symbols(self) -> dict:
        now = time.time()
        if self._local_cache_payload is not None and now < self._local_cache_expires_at:
            return self._local_cache_payload

        root = _resolve_symbol_root()
        root.mkdir(parents=True, exist_ok=True)
        items = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            if not rel.endswith(_ALLOWED_SUFFIXES):
                continue
            items.append({
                "path": rel,
                "size": p.stat().st_size,
                "mtime": int(p.stat().st_mtime),
            })
        payload = {"root": str(root), "items": items, "total": len(items)}
        self._local_cache_payload = payload
        self._local_cache_expires_at = now + _LOCAL_INDEX_TTL_SECONDS
        return payload

    def download_symbols(self, paths: list[str]) -> dict:
        self._ensure_remote_index()
        allowed_paths = {item["path"] for item in self._remote_index}

        root = _resolve_symbol_root()
        root.mkdir(parents=True, exist_ok=True)

        downloaded = []
        skipped = []
        failed = []

        for rel_path in paths:
            rel = str(rel_path or "").strip().lstrip("/")
            if not rel:
                continue
            if rel not in allowed_paths:
                failed.append({"path": rel, "reason": "not_found_in_remote_index"})
                continue
            target = (root / rel).resolve()
            if not str(target).startswith(str(root.resolve())):
                failed.append({"path": rel, "reason": "invalid_target_path"})
                continue
            if target.exists():
                skipped.append({"path": rel, "reason": "exists"})
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"{_RAW_BASE}/{self._default_branch}/{rel}"
            try:
                resp = self._session.get(url, timeout=60)
                resp.raise_for_status()
                target.write_bytes(resp.content)
                downloaded.append({"path": rel, "size": len(resp.content)})
            except Exception as e:
                logger.warning("Failed to download symbol %s: %s", rel, e)
                failed.append({"path": rel, "reason": str(e)})

        # Invalidate local index cache so new files become visible immediately.
        self._local_cache_payload = None
        self._local_cache_expires_at = 0.0

        return {
            "downloaded": downloaded,
            "skipped": skipped,
            "failed": failed,
            "root": str(root),
        }


_symbol_service: Optional[SymbolService] = None


def get_symbol_service() -> SymbolService:
    global _symbol_service
    if _symbol_service is None:
        _symbol_service = SymbolService()
    return _symbol_service
