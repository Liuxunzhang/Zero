"""SQLite catalogue and immutable on-disk package/version store."""

from __future__ import annotations

import hashlib
import difflib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .compiler import (
    MANIFEST_NAME,
    compile_package,
    inspect_package,
    normalize_relative_path,
    package_digest,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .zipio import export_package, safe_extract_zip

_STORE: "YaraXStore | None" = None
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9._-]+", "-", str(value).strip().lower()).strip("-._")
    return result[:64] or f"package-{uuid.uuid4().hex[:8]}"


def _sha(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=False)


class YaraXStore:
    """Owns the YARA-X data root.

    Database mutations and directory switches are serialized inside one
    process. Version directories are immutable after insertion.
    """

    def __init__(self, root: str | Path | None = None):
        if root is None:
            from zero import config
            root = getattr(config, "YARAX_DATA_DIR", ".zero/yarax")
        path = Path(root).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        self.root = path.resolve()
        self.db_path = self.root / "catalog.sqlite3"
        self.packages_dir = self.root / "packages"
        self.drafts_dir = self.root / "drafts"
        self.previews_dir = self.root / "previews"
        self.compile_cache_dir = self.root / "compile_cache"
        self.result_cache_dir = self.root / "result_cache"
        self.market_cache_dir = self.root / "market_cache"
        self.exports_dir = self.root / "exports"
        for directory in (
            self.root, self.packages_dir, self.drafts_dir, self.previews_dir,
            self.compile_cache_dir, self.result_cache_dir, self.market_cache_dir,
            self.exports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()
        self.cleanup_previews()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS packages (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    active_version_id TEXT,
                    source_type TEXT NOT NULL DEFAULT 'local',
                    source_json TEXT NOT NULL DEFAULT '{}',
                    forked_from TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    package_id TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    version TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    UNIQUE(package_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    package_id TEXT PRIMARY KEY REFERENCES packages(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL DEFAULT 1,
                    base_version_id TEXT,
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS import_previews (
                    token TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    info_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS market_sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    ref TEXT NOT NULL DEFAULT 'main',
                    subdirectory TEXT NOT NULL DEFAULT '',
                    license_hint TEXT NOT NULL DEFAULT '',
                    builtin INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                """
            )

    # -- package catalogue -------------------------------------------------

    def list_packages(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, v.version active_version, v.content_digest,
                       v.created_at version_created_at,
                       d.revision draft_revision
                FROM packages p
                LEFT JOIN versions v ON v.id=p.active_version_id
                LEFT JOIN drafts d ON d.package_id=p.id
                ORDER BY lower(p.name), p.id
                """
            ).fetchall()
        return [self._package_dict(row) for row in rows]

    def _package_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        source = json.loads(row["source_json"] or "{}")
        return {
            "id": row["id"], "plugin": f"package.{row['id']}", "name": row["name"],
            "description": row["description"], "enabled": bool(row["enabled"]),
            "active_version_id": row["active_version_id"],
            "version": row["active_version"] if "active_version" in keys else None,
            "content_digest": row["content_digest"] if "content_digest" in keys else None,
            "source_type": row["source_type"], "source": source,
            "forked_from": row["forked_from"],
            "draft_revision": row["draft_revision"] if "draft_revision" in keys else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "status": (
                "enabled" if row["enabled"] and row["active_version_id"]
                else "disabled" if row["active_version_id"] else "draft"
            ),
            "update_available": bool(source.get("update_available")),
        }

    def get_package(self, package_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, v.version active_version, v.content_digest,
                       d.revision draft_revision
                FROM packages p
                LEFT JOIN versions v ON v.id=p.active_version_id
                LEFT JOIN drafts d ON d.package_id=p.id
                WHERE p.id=?
                """, (package_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Rule package not found: {package_id}")
        return self._package_dict(row)

    def create_package(
        self,
        *,
        name: str,
        package_id: str | None = None,
        description: str = "",
        source_type: str = "local",
        source: dict[str, Any] | None = None,
        forked_from: str | None = None,
        initial_files: dict[str, str | bytes] | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        package_id = package_id or _slug(name)
        if not _ID_RE.fullmatch(package_id):
            raise ValidationError("package_id must match [a-z0-9][a-z0-9._-]{0,63}")
        with self._lock:
            draft = self.drafts_dir / package_id
            if draft.exists():
                raise ConflictError(f"Package already exists: {package_id}")
            tmp = Path(tempfile.mkdtemp(prefix=f".{package_id}-", dir=self.drafts_dir))
            manifest = {
                "schema_version": 1, "name": name, "version": "1.0.0",
                "description": description, "license": "", "homepage": "",
                "entrypoints": ["main.yar"], "globals": {},
                **(manifest or {}),
            }
            files = initial_files or {
                "main.yar": (
                    f"// {name}\nrule package_ready {{\n"
                    '    condition:\n        false\n}\n'
                )
            }
            try:
                for rel, content in files.items():
                    rel = normalize_relative_path(rel)
                    target = tmp / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
                (tmp / MANIFEST_NAME).write_text(_json(manifest) + "\n", encoding="utf-8")
                os.replace(tmp, draft)
                now = _now()
                with self._connect() as conn:
                    conn.execute(
                        """INSERT INTO packages
                           (id,name,description,enabled,source_type,source_json,forked_from,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (package_id, name, description, 0, source_type, _json(source or {}),
                         forked_from, now, now),
                    )
                    conn.execute(
                        """INSERT INTO drafts(package_id,revision,manifest_json,updated_at)
                           VALUES(?,?,?,?)""",
                        (package_id, 1, _json(manifest), now),
                    )
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                if draft.exists():
                    shutil.rmtree(draft, ignore_errors=True)
                raise
        return self.get_package(package_id)

    def set_enabled(self, package_id: str, enabled: bool) -> dict[str, Any]:
        package = self.get_package(package_id)
        if enabled and not package["active_version_id"]:
            raise ValidationError("A package must have a valid committed version before it can be enabled")
        with self._connect() as conn:
            conn.execute(
                "UPDATE packages SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), _now(), package_id),
            )
        return self.get_package(package_id)

    def delete_package(self, package_id: str) -> None:
        self.get_package(package_id)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM packages WHERE id=?", (package_id,))
            for directory in (self.packages_dir / package_id, self.drafts_dir / package_id):
                shutil.rmtree(directory, ignore_errors=True)

    def enabled_packages(self) -> list[dict[str, Any]]:
        return [
            package for package in self.list_packages()
            if package["enabled"] and package["active_version_id"]
        ]

    # -- drafts ------------------------------------------------------------

    def _draft_row(self, package_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE package_id=?", (package_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Draft not found for package: {package_id}")
        return row

    def ensure_draft(self, package_id: str) -> dict[str, Any]:
        package = self.get_package(package_id)
        try:
            return self.get_draft(package_id)
        except NotFoundError:
            pass
        version = self.get_version(package_id, package["active_version_id"])
        source = Path(version["path"])
        draft = self.drafts_dir / package_id
        tmp = Path(tempfile.mkdtemp(prefix=f".{package_id}-", dir=self.drafts_dir))
        try:
            _copy_tree(source, tmp / "tree")
            os.replace(tmp / "tree", draft)
            shutil.rmtree(tmp, ignore_errors=True)
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO drafts(package_id,revision,base_version_id,manifest_json,updated_at)
                       VALUES(?,?,?,?,?)""",
                    (package_id, 1, package["active_version_id"], _json(version["manifest"]), _now()),
                )
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return self.get_draft(package_id)

    def get_draft(self, package_id: str) -> dict[str, Any]:
        row = self._draft_row(package_id)
        root = self.drafts_dir / package_id
        files = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                rel = path.relative_to(root).as_posix()
                files.append({
                    "path": rel, "size": path.stat().st_size, "sha256": _sha(path),
                    "is_rule": path.suffix.lower() in {".yar", ".yara"},
                })
        return {
            "package_id": package_id, "revision": row["revision"],
            "base_version_id": row["base_version_id"],
            "manifest": json.loads(row["manifest_json"] or "{}"),
            "files": files, "updated_at": row["updated_at"],
        }

    def read_draft_file(self, package_id: str, path: str) -> dict[str, Any]:
        draft = self.get_draft(package_id)
        rel = normalize_relative_path(path)
        target = self.drafts_dir / package_id / rel
        if not target.is_file() or target.is_symlink():
            raise NotFoundError(f"Draft file not found: {rel}")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeError:
            raise ValidationError("Only UTF-8 text files can be edited")
        return {
            "path": rel, "content": content, "sha256": _sha(target),
            "revision": draft["revision"],
        }

    def search_draft(
        self, package_id: str, query: str, *, max_results: int = 200
    ) -> list[dict[str, Any]]:
        self._draft_row(package_id)
        needle = str(query or "").casefold()
        if not needle:
            return []
        results = []
        root = self.drafts_dir / package_id
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 10 * 1024 * 1024:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for line_number, line in enumerate(lines, 1):
                column = line.casefold().find(needle)
                if column < 0:
                    continue
                results.append({
                    "path": path.relative_to(root).as_posix(), "line": line_number,
                    "column": column + 1, "preview": line[:500],
                })
                if len(results) >= max_results:
                    return results
        return results

    def write_draft_file(
        self,
        package_id: str,
        path: str,
        content: str,
        *,
        base_revision: int,
        file_sha: str | None = None,
    ) -> dict[str, Any]:
        rel = normalize_relative_path(path)
        if len(content.encode("utf-8")) > 10 * 1024 * 1024:
            raise ValidationError("Editable file exceeds 10 MiB")
        with self._lock:
            row = self._draft_row(package_id)
            if int(base_revision) != int(row["revision"]):
                raise ConflictError("Draft revision changed; reload before saving")
            target = self.drafts_dir / package_id / rel
            if file_sha is not None and _sha(target) != file_sha:
                raise ConflictError("File content changed; reload before saving")
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".edit-", dir=target.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, target)
            except Exception:
                Path(tmp_name).unlink(missing_ok=True)
                raise
            revision = int(row["revision"]) + 1
            manifest_json = row["manifest_json"]
            if rel == MANIFEST_NAME:
                try:
                    manifest_json = _json(json.loads(content))
                except json.JSONDecodeError:
                    # Invalid manifests are allowed in a draft; keep the last
                    # parsed copy in SQLite and validate the file on demand.
                    pass
            with self._connect() as conn:
                conn.execute(
                    "UPDATE drafts SET revision=?,manifest_json=?,updated_at=? WHERE package_id=?",
                    (revision, manifest_json, _now(), package_id),
                )
                conn.execute("UPDATE packages SET updated_at=? WHERE id=?", (_now(), package_id))
        return self.read_draft_file(package_id, rel)

    def delete_draft_file(self, package_id: str, path: str, base_revision: int) -> dict[str, Any]:
        rel = normalize_relative_path(path)
        if rel == MANIFEST_NAME:
            raise ValidationError(f"{MANIFEST_NAME} cannot be deleted")
        with self._lock:
            row = self._draft_row(package_id)
            if int(base_revision) != int(row["revision"]):
                raise ConflictError("Draft revision changed; reload before deleting")
            target = self.drafts_dir / package_id / rel
            if not target.is_file() or target.is_symlink():
                raise NotFoundError(f"Draft file not found: {rel}")
            target.unlink()
            parent = target.parent
            root = self.drafts_dir / package_id
            while parent != root and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            with self._connect() as conn:
                conn.execute(
                    "UPDATE drafts SET revision=revision+1,updated_at=? WHERE package_id=?",
                    (_now(), package_id),
                )
        return self.get_draft(package_id)

    def rename_draft_file(
        self, package_id: str, old_path: str, new_path: str, base_revision: int
    ) -> dict[str, Any]:
        old = normalize_relative_path(old_path)
        new = normalize_relative_path(new_path)
        with self._lock:
            row = self._draft_row(package_id)
            if int(base_revision) != int(row["revision"]):
                raise ConflictError("Draft revision changed; reload before renaming")
            root = self.drafts_dir / package_id
            source, target = root / old, root / new
            if not source.is_file() or source.is_symlink():
                raise NotFoundError(f"Draft file not found: {old}")
            if target.exists():
                raise ConflictError(f"Draft file already exists: {new}")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            with self._connect() as conn:
                conn.execute(
                    "UPDATE drafts SET revision=revision+1,updated_at=? WHERE package_id=?",
                    (_now(), package_id),
                )
        return self.get_draft(package_id)

    def validate_draft(
        self, package_id: str, manifest_override: dict[str, Any] | None = None,
        relaxed_regex: bool = False,
    ) -> dict[str, Any]:
        self._draft_row(package_id)
        root = self.drafts_dir / package_id
        try:
            _, info = compile_package(root, manifest_override, relaxed_regex=relaxed_regex)
            return {"valid": True, "diagnostics": [], **info}
        except ValidationError as exc:
            return {"valid": False, "diagnostics": exc.diagnostics or [{
                "severity": "error", "code": "package_validation", "message": str(exc),
                "file": None, "line": None, "column": None, "span": None,
            }]}

    def commit_draft(
        self, package_id: str, *, base_revision: int | None = None,
        manifest_override: dict[str, Any] | None = None, relaxed_regex: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._draft_row(package_id)
            if base_revision is not None and int(base_revision) != int(row["revision"]):
                raise ConflictError("Draft revision changed; reload before committing")
            root = self.drafts_dir / package_id
            rules, info = compile_package(root, manifest_override, relaxed_regex=relaxed_regex)
            del rules
            return self._install_version(
                package_id, root, info, base_version_id=row["base_version_id"], activate=True
            )

    # -- immutable versions ------------------------------------------------

    def _install_version(
        self, package_id: str, source: Path, info: dict[str, Any],
        *, base_version_id: str | None = None, activate: bool,
    ) -> dict[str, Any]:
        self.get_package(package_id)
        version_id = uuid.uuid4().hex
        package_versions = self.packages_dir / package_id / "versions"
        package_versions.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".version-", dir=package_versions))
        final = package_versions / version_id
        _copy_tree(source, tmp / "tree")
        # Store the normalized manifest even when the imported file omitted it.
        (tmp / "tree" / MANIFEST_NAME).write_text(
            _json(info["manifest"]) + "\n", encoding="utf-8"
        )
        try:
            os.replace(tmp / "tree", final)
            shutil.rmtree(tmp, ignore_errors=True)
            with self._connect() as conn:
                sequence = conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM versions WHERE package_id=?",
                    (package_id,),
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO versions
                       (id,package_id,sequence,version,content_digest,manifest_json,
                        relative_path,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        version_id, package_id, sequence,
                        str(info["manifest"].get("version") or sequence),
                        info["content_digest"], _json(info["manifest"]),
                        str(final.relative_to(self.root)), _now(),
                    ),
                )
                if activate:
                    conn.execute(
                        """UPDATE packages SET active_version_id=?,enabled=1,name=?,
                           description=?,updated_at=? WHERE id=?""",
                        (
                            version_id, str(info["manifest"].get("name") or package_id),
                            str(info["manifest"].get("description") or ""), _now(), package_id,
                        ),
                    )
                conn.execute("DELETE FROM drafts WHERE package_id=?", (package_id,))
            shutil.rmtree(self.drafts_dir / package_id, ignore_errors=True)
        except Exception:
            shutil.rmtree(final, ignore_errors=True)
            raise
        return self.get_version(package_id, version_id)

    def list_versions(self, package_id: str) -> list[dict[str, Any]]:
        self.get_package(package_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM versions WHERE package_id=? ORDER BY sequence DESC",
                (package_id,),
            ).fetchall()
        return [self._version_dict(row) for row in rows]

    def _version_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "package_id": row["package_id"], "sequence": row["sequence"],
            "version": row["version"], "content_digest": row["content_digest"],
            "manifest": json.loads(row["manifest_json"]),
            "path": str(self.root / row["relative_path"]),
            "diagnostics": json.loads(row["diagnostics_json"] or "[]"),
            "created_at": row["created_at"],
        }

    def get_version(self, package_id: str, version_id: str | None = None) -> dict[str, Any]:
        if version_id is None:
            version_id = self.get_package(package_id)["active_version_id"]
        if not version_id:
            raise NotFoundError(f"No active version for package: {package_id}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM versions WHERE package_id=? AND id=?",
                (package_id, version_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Version not found: {version_id}")
        return self._version_dict(row)

    def restore_version(self, package_id: str, version_id: str) -> dict[str, Any]:
        version = self.get_version(package_id, version_id)
        source = Path(version["path"])
        _, info = compile_package(source)
        info["manifest"]["version"] = str(info["manifest"].get("version") or "") + "-restored"
        info["content_digest"] = package_digest(source, info["files"], info["manifest"])
        return self._install_version(
            package_id, source, info, base_version_id=version_id, activate=True
        )

    def version_diff(
        self, package_id: str, version_id: str, other_version_id: str | None = None
    ) -> dict[str, Any]:
        left = self.get_version(package_id, version_id)
        if other_version_id:
            right = self.get_version(package_id, other_version_id)
        else:
            history = self.list_versions(package_id)
            older = next(
                (item for item in history if item["sequence"] < left["sequence"]),
                None,
            )
            if older is None:
                return {"added": [], "modified": [], "deleted": [], "diffs": {}}
            right = older

        def snapshot(version):
            root = Path(version["path"])
            return {
                path.relative_to(root).as_posix(): path
                for path in root.rglob("*") if path.is_file()
            }

        new_files, old_files = snapshot(left), snapshot(right)
        added = sorted(set(new_files) - set(old_files))
        deleted = sorted(set(old_files) - set(new_files))
        modified = sorted(
            name for name in set(new_files) & set(old_files)
            if _sha(new_files[name]) != _sha(old_files[name])
        )
        diffs = {}
        for name in modified:
            try:
                old_text = old_files[name].read_text(encoding="utf-8").splitlines(True)
                new_text = new_files[name].read_text(encoding="utf-8").splitlines(True)
            except (OSError, UnicodeError):
                continue
            text = "".join(difflib.unified_diff(
                old_text, new_text, fromfile=f"{right['version']}/{name}",
                tofile=f"{left['version']}/{name}", n=3,
            ))
            diffs[name] = text[:500_000]
        return {
            "from_version_id": right["id"], "to_version_id": left["id"],
            "added": added, "modified": modified, "deleted": deleted, "diffs": diffs,
        }

    def rollback(self, package_id: str, version_id: str) -> dict[str, Any]:
        self.get_version(package_id, version_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE packages SET active_version_id=?,enabled=1,updated_at=? WHERE id=?",
                (version_id, _now(), package_id),
            )
        return self.get_package(package_id)

    def export(self, package_id: str) -> Path:
        package = self.get_package(package_id)
        version = self.get_version(package_id, package["active_version_id"])
        filename = f"{package_id}-{version['version']}.zip"
        work = Path(tempfile.mkdtemp(prefix=".export-", dir=self.exports_dir))
        try:
            tree = work / "tree"
            _copy_tree(Path(version["path"]), tree)
            source = {
                "source_type": package["source_type"],
                "source": package["source"],
                "forked_from": package["forked_from"],
                "exported_version_id": version["id"],
                "content_digest": version["content_digest"],
            }
            (tree / "zero-source.json").write_text(
                _json(source) + "\n", encoding="utf-8"
            )
            return export_package(tree, self.exports_dir / filename)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def fork_package(self, package_id: str, name: str | None = None) -> dict[str, Any]:
        upstream = self.get_package(package_id)
        existing = next(
            (package for package in self.list_packages() if package["forked_from"] == package_id),
            None,
        )
        if existing is not None:
            self.ensure_draft(existing["id"])
            return self.get_package(existing["id"])
        version = self.get_version(package_id, upstream["active_version_id"])
        new_name = name or f"{upstream['name']} (local)"
        created = self.create_package(
            name=new_name, source_type="local", forked_from=package_id,
            initial_files={
                path.relative_to(Path(version["path"])).as_posix(): path.read_bytes()
                for path in Path(version["path"]).rglob("*")
                if path.is_file()
            },
            manifest={**version["manifest"], "name": new_name},
        )
        return created

    # -- ZIP previews ------------------------------------------------------

    def preview_zip(
        self, archive: Path, *, entrypoints: list[str] | None = None,
        ttl_seconds: int = 1800,
    ) -> dict[str, Any]:
        self.cleanup_previews()
        token = uuid.uuid4().hex
        root = self.previews_dir / token
        safe_extract_zip(Path(archive), root)
        override = {"entrypoints": entrypoints} if entrypoints is not None else None
        try:
            info = inspect_package(root, override)
            result = {"valid": True, "diagnostics": [], **info}
            try:
                compile_package(root, info["manifest"])
            except ValidationError as exc:
                result["valid"] = False
                result["diagnostics"] = exc.diagnostics or [{
                    "severity": "error", "code": "package_validation",
                    "message": str(exc), "file": None, "line": None,
                    "column": None, "span": None,
                }]
        except ValidationError as exc:
            result = {
                "valid": False, "manifest": {}, "files": [], "rule_files": [],
                "entrypoints": entrypoints or [], "include_graph": {},
                "content_digest": None,
                "diagnostics": exc.diagnostics or [{
                    "severity": "error", "code": "package_validation",
                    "message": str(exc), "file": None, "line": None,
                    "column": None, "span": None,
                }],
            }
        expires_at = _now() + ttl_seconds
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO import_previews(token,relative_path,info_json,expires_at) VALUES(?,?,?,?)",
                (token, str(root.relative_to(self.root)), _json(result), expires_at),
            )
        return {"token": token, "expires_at": expires_at, **result}

    def confirm_preview(
        self, token: str, *, package_id: str | None = None,
        entrypoints: list[str] | None = None, save_as_draft: bool = False,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM import_previews WHERE token=? AND consumed=0", (token,)
            ).fetchone()
            if row is None or row["expires_at"] < _now():
                raise NotFoundError("Import preview token is invalid or expired")
            conn.execute("UPDATE import_previews SET consumed=1 WHERE token=?", (token,))
        root = self.root / row["relative_path"]
        stored = json.loads(row["info_json"])
        override = {"entrypoints": entrypoints} if entrypoints is not None else None
        manifest = stored.get("manifest") or {}
        if override:
            manifest.update(override)
        name = str(manifest.get("name") or package_id or "Imported YARA rules")
        initial_files = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()
        }
        package = self.create_package(
            name=name, package_id=package_id, description=str(manifest.get("description") or ""),
            source_type="zip", initial_files=initial_files, manifest=manifest,
        )
        validation = self.validate_draft(package["id"], manifest)
        if validation["valid"] and not save_as_draft:
            version = self.commit_draft(package["id"], manifest_override=manifest)
            package = self.get_package(package["id"])
            package["installed_version"] = version
        elif not validation["valid"]:
            package["diagnostics"] = validation["diagnostics"]
            package["status"] = "draft"
        shutil.rmtree(root, ignore_errors=True)
        return package

    def cleanup_previews(self) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT token,relative_path FROM import_previews WHERE expires_at<? OR consumed=1",
                (_now(),),
            ).fetchall()
            for row in rows:
                shutil.rmtree(self.root / row["relative_path"], ignore_errors=True)
                conn.execute("DELETE FROM import_previews WHERE token=?", (row["token"],))
        return len(rows)

    def clear_result_cache(self, package_id: str | None = None) -> None:
        if package_id is None:
            shutil.rmtree(self.result_cache_dir, ignore_errors=True)
            self.result_cache_dir.mkdir(parents=True, exist_ok=True)
            return
        for path in self.result_cache_dir.glob(f"{package_id}-*.json"):
            path.unlink(missing_ok=True)


def get_yarax_store() -> YaraXStore:
    global _STORE
    if _STORE is None:
        _STORE = YaraXStore()
    return _STORE
