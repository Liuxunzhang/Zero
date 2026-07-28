"""Constrained GitHub-only YARA rule marketplace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from .compiler import (
    compile_package,
    inspect_package,
    normalize_relative_path,
    package_digest,
)
from .errors import NotFoundError, ValidationError
from .store import YaraXStore, get_yarax_store
from .zipio import MAX_ZIP_BYTES, safe_extract_zip

_ALLOWED_HOSTS = {
    "api.github.com", "github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "codeload.github.com",
}
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")

BUILTIN_SOURCES = [
    {
        "id": "yara-forge-core", "name": "YARA Forge Core",
        "owner": "YARAHQ", "repo": "yara-forge", "ref": "main",
        "subdirectory": "core", "license_hint": "Check upstream per-rule licenses",
        "variant": "core",
    },
    {
        "id": "yara-forge-extended", "name": "YARA Forge Extended",
        "owner": "YARAHQ", "repo": "yara-forge", "ref": "main",
        "subdirectory": "extended", "license_hint": "Check upstream per-rule licenses",
        "variant": "extended",
    },
    {
        "id": "yara-forge-full", "name": "YARA Forge Full",
        "owner": "YARAHQ", "repo": "yara-forge", "ref": "main",
        "subdirectory": "full", "license_hint": "Check upstream per-rule licenses",
        "variant": "full",
    },
    {
        "id": "signature-base", "name": "Neo23x0 signature-base",
        "owner": "Neo23x0", "repo": "signature-base", "ref": "master",
        "subdirectory": "yara", "license_hint": "Detection Rule License; external variables may be required",
    },
    {
        "id": "reversinglabs", "name": "ReversingLabs YARA Rules",
        "owner": "reversinglabs", "repo": "reversinglabs-yara-rules", "ref": "develop",
        "subdirectory": "yara", "license_hint": "MIT",
    },
]


class GitHubClient:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        from zero import config
        self.token = (
            os.environ.get("ZERO_GITHUB_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or getattr(config, "SYMBOL_GITHUB_TOKEN", "")
            or ""
        )
        self.ttl = int(getattr(config, "YARAX_MARKET_TTL_SECONDS", 6 * 3600))
        self.stale = int(getattr(config, "SYMBOL_INDEX_STALE_SECONDS", 7 * 24 * 3600))

    @staticmethod
    def _check_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
            raise ValidationError("Marketplace network access is restricted to GitHub")
        if parsed.username or parsed.password:
            raise ValidationError("Credentials in marketplace URLs are not allowed")

    def _headers(self, etag: str = "") -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "zero-yarax/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def api_json(self, path: str, *, force: bool = False) -> dict:
        if not path.startswith("/") or "//" in path:
            raise ValidationError("Invalid GitHub API path")
        url = "https://api.github.com" + path
        self._check_url(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        cache = self.cache_dir / f"{key}.json"
        cached = {}
        if cache.is_file():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if not force and time.time() - cached.get("fetched_at", 0) < self.ttl:
                    return cached["payload"]
            except (OSError, ValueError, KeyError):
                cached = {}
        try:
            response = requests.get(
                url, headers=self._headers(cached.get("etag", "")),
                timeout=(10, 30), allow_redirects=False,
            )
        except requests.RequestException:
            if cached and time.time() - cached.get("fetched_at", 0) <= self.stale:
                return cached["payload"]
            raise
        if response.is_redirect:
            raise ValidationError("Cross-domain redirects are not allowed")
        if response.status_code == 304 and cached:
            cached["fetched_at"] = time.time()
            cache.write_text(json.dumps(cached), encoding="utf-8")
            return cached["payload"]
        if response.status_code >= 500 and cached and (
            time.time() - cached.get("fetched_at", 0) <= self.stale
        ):
            return cached["payload"]
        response.raise_for_status()
        payload = response.json()
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "fetched_at": time.time(), "etag": response.headers.get("ETag", ""),
            "payload": payload,
        }), encoding="utf-8")
        os.replace(tmp, cache)
        return payload

    def download_archive(self, owner: str, repo: str, ref: str, output: Path) -> Path:
        url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"
        return self.download_url(url, output)

    def download_url(self, url: str, output: Path) -> Path:
        """Download through a short, explicitly allowlisted redirect chain."""
        self._check_url(url)
        response = None
        for _ in range(4):
            response = requests.get(
                url, headers=self._headers(), stream=True, timeout=(10, 120),
                allow_redirects=False,
            )
            if not response.is_redirect:
                break
            location = response.headers.get("Location") or ""
            self._check_url(location)
            response.close()
            url = location
        if response is None or response.is_redirect:
            raise ValidationError("Marketplace archive redirect chain is too long")
        response.raise_for_status()
        total = 0
        with Path(output).open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                total += len(chunk)
                if total > MAX_ZIP_BYTES:
                    raise ValidationError("Marketplace archive exceeds 50 MiB")
                handle.write(chunk)
        return Path(output)


class MarketService:
    def __init__(self, store: YaraXStore | None = None):
        self.store = store or get_yarax_store()
        self.client = GitHubClient(self.store.market_cache_dir)
        self._ensure_builtins()

    def _ensure_builtins(self) -> None:
        with self.store._connect() as conn:
            for source in BUILTIN_SOURCES:
                conn.execute(
                    """INSERT OR REPLACE INTO market_sources
                       (id,name,owner,repo,ref,subdirectory,license_hint,builtin,created_at)
                       VALUES(?,?,?,?,?,?,?,?,COALESCE(
                         (SELECT created_at FROM market_sources WHERE id=?),?))""",
                    (
                        source["id"], source["name"], source["owner"], source["repo"],
                        source["ref"], source["subdirectory"], source["license_hint"], 1,
                        source["id"], time.time(),
                    ),
                )

    def list_sources(self) -> list[dict]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT * FROM market_sources ORDER BY builtin DESC,name").fetchall()
        packages = self.store.list_packages()
        result = []
        for row in rows:
            installed = next(
                (p for p in packages if p["source"].get("market_source_id") == row["id"]), None
            )
            result.append({
                "id": row["id"], "name": row["name"],
                "repository": f"{row['owner']}/{row['repo']}", "owner": row["owner"],
                "repo": row["repo"], "ref": row["ref"],
                "subdirectory": row["subdirectory"], "license": row["license_hint"],
                "builtin": bool(row["builtin"]), "installed": bool(installed),
                "installed_package_id": installed["id"] if installed else None,
            })
        return result

    def add_source(
        self, repository: str, *, name: str = "", ref: str = "main",
        subdirectory: str = "",
    ) -> dict:
        repository = repository.strip()
        if not _REPO_RE.fullmatch(repository):
            raise ValidationError("Custom sources must be a GitHub owner/repo")
        if not _REF_RE.fullmatch(ref) or ".." in ref:
            raise ValidationError("Invalid GitHub ref")
        if subdirectory:
            subdirectory = normalize_relative_path(subdirectory)
        owner, repo = repository.split("/", 1)
        source_id = "github-" + hashlib.sha256(
            f"{repository}:{ref}:{subdirectory}".encode()
        ).hexdigest()[:12]
        with self.store._connect() as conn:
            conn.execute(
                """INSERT INTO market_sources
                   (id,name,owner,repo,ref,subdirectory,license_hint,builtin,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    source_id, name.strip() or repository, owner, repo, ref,
                    subdirectory, "Check upstream repository license", 0, time.time(),
                ),
            )
        return next(item for item in self.list_sources() if item["id"] == source_id)

    def delete_source(self, source_id: str) -> None:
        with self.store._connect() as conn:
            row = conn.execute("SELECT builtin FROM market_sources WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise NotFoundError("Marketplace source not found")
            if row["builtin"]:
                raise ValidationError("Built-in marketplace sources cannot be deleted")
            conn.execute("DELETE FROM market_sources WHERE id=?", (source_id,))

    def _source(self, source_id: str):
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM market_sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise NotFoundError("Marketplace source not found")
        return row

    @staticmethod
    def _manifest_override(row) -> dict:
        if row["id"] == "signature-base":
            globals_spec = {
                name: {"type": "string", "default": ""}
                for name in ("filename", "filepath", "extension", "filetype", "md5", "owner")
            }
            return {
                "name": row["name"], "license": row["license_hint"],
                "description": (
                    "Upstream rules use external variables: filename, filepath, "
                    "extension, filetype, md5 and owner."
                ),
                "globals": globals_spec,
            }
        return {"name": row["name"], "license": row["license_hint"]}

    def _inspect_downloaded(self, source: Path, row, upstream: dict) -> dict:
        override = self._manifest_override(row)
        override["version"] = str(upstream.get("upstream_version") or row["ref"])
        info = inspect_package(source, override)
        info["content_digest"] = package_digest(source, info["files"], info["manifest"])
        return info

    def inspect_source(self, source_id: str, *, force: bool = False) -> dict:
        row = self._source(source_id)
        if source_id.startswith("yara-forge-"):
            release = self.client.api_json(
                f"/repos/{row['owner']}/{row['repo']}/releases/latest", force=force
            )
            variant = source_id.removeprefix("yara-forge-")
            asset_name = f"yara-forge-rules-{variant}.zip"
            asset = next(
                (item for item in release.get("assets", []) if item.get("name") == asset_name),
                None,
            )
            if asset is None:
                raise ValidationError(f"YARA Forge release asset is missing: {asset_name}")
            return {
                "id": source_id, "upstream_version": release.get("tag_name"),
                "updated_at": release.get("published_at"), "homepage": release.get("html_url"),
                "license": row["license_hint"], "size": asset.get("size"),
                "download_url": asset.get("browser_download_url"),
            }
        commit = self.client.api_json(
            f"/repos/{row['owner']}/{row['repo']}/commits/{row['ref']}", force=force
        )
        return {
            "id": source_id, "upstream_version": commit.get("sha"),
            "updated_at": commit.get("commit", {}).get("committer", {}).get("date"),
            "homepage": commit.get("html_url"),
            "license": row["license_hint"],
        }

    def _download_tree(self, source_id: str) -> tuple[Path, sqlite3.Row, dict]:
        row = self._source(source_id)
        work = Path(tempfile.mkdtemp(prefix="market-", dir=self.store.market_cache_dir))
        archive = work / "source.zip"
        try:
            upstream = self.inspect_source(source_id)
            archive_dir = self.store.market_cache_dir / "archives"
            archive_dir.mkdir(parents=True, exist_ok=True)
            version_key = re.sub(
                r"[^A-Za-z0-9._-]+", "-", str(upstream.get("upstream_version") or row["ref"])
            )
            cached_archive = archive_dir / f"{source_id}-{version_key}.zip"
            if cached_archive.is_file():
                shutil.copy2(cached_archive, archive)
            else:
                downloaded = archive.with_suffix(".download")
                if source_id.startswith("yara-forge-"):
                    self.client.download_url(upstream["download_url"], downloaded)
                else:
                    self.client.download_archive(
                        row["owner"], row["repo"], row["ref"], downloaded
                    )
                os.replace(downloaded, cached_archive)
                shutil.copy2(cached_archive, archive)
            extracted = safe_extract_zip(archive, work / "tree")
            source = extracted
            if row["subdirectory"]:
                source = extracted.joinpath(*Path(row["subdirectory"]).parts)
            if not source.is_dir():
                raise ValidationError(
                    f"Marketplace subdirectory does not exist: {row['subdirectory']}"
                )
            selected = work / "selected"
            shutil.copytree(source, selected)
            return selected, row, upstream
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise

    def install(self, source_id: str, *, package_id: str | None = None) -> dict:
        source, row, upstream = self._download_tree(source_id)
        work = source.parent
        try:
            info = self._inspect_downloaded(source, row, upstream)
            compatible = True
            diagnostics = []
            try:
                compile_package(source, info["manifest"])
            except ValidationError as exc:
                compatible = False
                diagnostics = exc.diagnostics or [{
                    "severity": "error", "code": "package_validation",
                    "message": str(exc), "file": None, "line": None,
                    "column": None, "span": None,
                }]
            package = self.store.create_package(
                name=info["manifest"].get("name") or row["name"],
                package_id=package_id,
                description=info["manifest"].get("description") or "",
                source_type="market",
                source={
                    "market_source_id": source_id,
                    "repository": f"{row['owner']}/{row['repo']}",
                    "ref": row["ref"], "subdirectory": row["subdirectory"],
                    "upstream_version": upstream.get("upstream_version"),
                    "upstream_digest": info["content_digest"],
                },
                initial_files={
                    p.relative_to(source).as_posix(): p.read_bytes()
                    for p in source.rglob("*") if p.is_file()
                },
                manifest=info["manifest"],
            )
            if compatible:
                self.store.commit_draft(package["id"], manifest_override=info["manifest"])
                package = self.store.get_package(package["id"])
                package["compatible"] = True
            else:
                package["compatible"] = False
                package["diagnostics"] = diagnostics
                package["status"] = "draft"
            return package
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def check_update(self, package_id: str) -> dict:
        package = self.store.get_package(package_id)
        source_id = package["source"].get("market_source_id")
        if not source_id:
            raise ValidationError("Package is not managed by the marketplace")
        source, row, upstream = self._download_tree(source_id)
        try:
            info = self._inspect_downloaded(source, row, upstream)
            active = self.store.get_version(package_id, package["active_version_id"])
            old_files = {
                p.relative_to(Path(active["path"])).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(active["path"]).rglob("*")
                if p.is_file() and p.name != "zero-yara.json"
            }
            new_files = {
                p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob("*") if p.is_file() and p.name != "zero-yara.json"
            }
            diagnostics = []
            compatible = True
            try:
                compile_package(source, info["manifest"])
            except ValidationError as exc:
                compatible = False
                diagnostics = exc.diagnostics or [{"severity": "error", "message": str(exc)}]
            result = {
                "package_id": package_id,
                "available": info["content_digest"] != active["content_digest"],
                "upstream_version": upstream.get("upstream_version"),
                "content_digest": info["content_digest"], "compatible": compatible,
                "diagnostics": diagnostics,
                "added": sorted(set(new_files) - set(old_files)),
                "deleted": sorted(set(old_files) - set(new_files)),
                "modified": sorted(
                    key for key in set(old_files) & set(new_files)
                    if old_files[key] != new_files[key]
                ),
            }
            source_json = {
                **package["source"],
                "update_available": result["available"],
                "checked_upstream_version": upstream.get("upstream_version"),
            }
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE packages SET source_json=?,updated_at=? WHERE id=?",
                    (json.dumps(source_json), time.time(), package_id),
                )
            return result
        finally:
            shutil.rmtree(source.parent, ignore_errors=True)

    def upgrade(self, package_id: str) -> dict:
        package = self.store.get_package(package_id)
        source_id = package["source"].get("market_source_id")
        if not source_id:
            raise ValidationError("Package is not managed by the marketplace")
        source, row, upstream = self._download_tree(source_id)
        try:
            info = self._inspect_downloaded(source, row, upstream)
            compile_package(source, info["manifest"])
            # Preserve the old active version if validation or installation fails.
            self.store.ensure_draft(package_id)
            draft_root = self.store.drafts_dir / package_id
            shutil.rmtree(draft_root)
            shutil.copytree(source, draft_root)
            version = self.store.commit_draft(package_id, manifest_override=info["manifest"])
            source_json = {
                **package["source"], "upstream_version": upstream.get("upstream_version"),
                "upstream_digest": info["content_digest"], "update_available": False,
            }
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE packages SET source_json=?,updated_at=? WHERE id=?",
                    (json.dumps(source_json), time.time(), package_id),
                )
            return version
        finally:
            shutil.rmtree(source.parent, ignore_errors=True)


_MARKET: MarketService | None = None


def get_market_service() -> MarketService:
    global _MARKET
    if _MARKET is None:
        _MARKET = MarketService()
    return _MARKET
