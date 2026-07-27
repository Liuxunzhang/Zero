"""Symbol table service for remote browsing and local download management.

GitHub REST rate limits (unauthenticated: 60/hr, token: 5000/hr) are handled by:
1. Optional token from config / env (SYMBOL_GITHUB_TOKEN / GITHUB_TOKEN)
2. Disk-backed remote index under .zero/symbols/remote_index/ (survives restarts)
3. Soft TTL + long stale-while-revalidate window
4. Prefer single recursive git/trees call; never burn quota on Contents walk when limited
5. Serve stale cache on 403/429 with a clear status message
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from zero import config
from zero.core.kernel_detector import (
    LinuxKernelDetection,
    LinuxKernelInfo,
    detect_linux_kernel,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_API_BASE = "https://api.github.com"
_LOCAL_INDEX_TTL_SECONDS = 10
_ALLOWED_SUFFIXES = (".json", ".json.xz", ".json.gz", ".zip")
_DISK_INDEX_DIR = _PROJECT_ROOT / ".zero" / "symbols" / "remote_index"

# Soft TTL: serve memory/disk without network. Hard stale: still serve if refresh fails.
_DEFAULT_INDEX_TTL_SECONDS = 6 * 3600
_DEFAULT_INDEX_STALE_SECONDS = 7 * 24 * 3600

# Parallel raw.githubusercontent.com downloads (raw host, not the REST quota).
_DOWNLOAD_WORKERS = 4
_DOWNLOAD_CHUNK_BYTES = 256 * 1024
_AUTO_SCAN_DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024
# Keep the proxy endpoint fixed in code.  The API accepts only a boolean, not
# an arbitrary proxy URL, so the download endpoint cannot be used as an SSRF
# primitive.
_GH_PROXY_BASE = "https://gh-proxy.com/"
_ProgressCallback = Optional[Callable[[dict[str, Any]], None]]

# Remote symbol index sources (GitHub owner/name). First entry is the default.
_DEFAULT_REPOS = (
    "Abyss-W4tcher/volatility3-symbols",
    "Sunmedalia/volatility3-symbols",
)

_LINUX_MARKERS = (
    "linux",
    "ubuntu",
    "debian",
    "centos",
    "rhel",
    "fedora",
    "alma",
    "rocky",
    "kali",
    "alpine",
    "arch",
    "suse",
    "oracle",
    "redhat",
)

# Terms used by the public symbol repositories.  These are deliberately kept
# separate from the banner detector: a distro name in a compiler string is a
# hint for ranking candidates, never a substitute for an exact kernel release
# match.
_DISTRO_PATH_MARKERS: dict[str, tuple[str, ...]] = {
    "ubuntu": ("ubuntu",),
    "debian": ("debian",),
    "kali": ("kali",),
    "almalinux": ("almalinux",),
    "rocky": ("rocky",),
    "centos": ("centos",),
    "rhel": ("rhel", "redhat", "red-hat"),
    "fedora": ("fedora",),
    "oracle": ("oracle",),
    "amazon": ("amazon", "amzn"),
    "suse": ("suse",),
    "arch": ("arch",),
}

_ARCH_PATH_MARKERS: dict[str, tuple[str, ...]] = {
    "x86_64": ("x86_64", "amd64"),
    "aarch64": ("aarch64", "arm64"),
    "arm": ("armv7", "armv6", "/arm/"),
    "ppc64le": ("ppc64le",),
    "s390x": ("s390x",),
    "i386": ("i386", "i486", "i586", "i686"),
}


@dataclass(frozen=True)
class _RepoRef:
    owner: str
    name: str

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def raw_base(self) -> str:
        return f"https://raw.githubusercontent.com/{self.owner}/{self.name}"

    @property
    def disk_key(self) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", self.full)
        return f"{safe}.json"


def _parse_repo(value: str) -> _RepoRef:
    text = (value or "").strip().strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    if "github.com/" in text:
        text = text.split("github.com/", 1)[1]
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"invalid repo: {value!r}")
    return _RepoRef(owner=parts[0], name=parts[1])


def _configured_repos() -> list[_RepoRef]:
    raw = getattr(config, "SYMBOL_REMOTE_REPOS", None)
    if raw is None:
        raw = _DEFAULT_REPOS
    repos: list[_RepoRef] = []
    seen: set[str] = set()
    for item in raw:
        try:
            ref = _parse_repo(str(item))
        except ValueError:
            logger.warning("Skip invalid symbol remote repo config: %s", item)
            continue
        if ref.full in seen:
            continue
        seen.add(ref.full)
        repos.append(ref)
    if not repos:
        repos = [_parse_repo(r) for r in _DEFAULT_REPOS]
    return repos


def _resolve_github_token() -> str:
    for key in ("SYMBOL_GITHUB_TOKEN", "GITHUB_TOKEN"):
        val = str(getattr(config, key, "") or "").strip()
        if val:
            return val
    for key in ("ZERO_GITHUB_TOKEN", "SYMBOL_GITHUB_TOKEN", "GITHUB_TOKEN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return ""


def _index_ttl_seconds() -> int:
    try:
        return max(60, int(getattr(config, "SYMBOL_INDEX_TTL_SECONDS", _DEFAULT_INDEX_TTL_SECONDS)))
    except (TypeError, ValueError):
        return _DEFAULT_INDEX_TTL_SECONDS


def _index_stale_seconds() -> int:
    try:
        return max(
            _index_ttl_seconds(),
            int(getattr(config, "SYMBOL_INDEX_STALE_SECONDS", _DEFAULT_INDEX_STALE_SECONDS)),
        )
    except (TypeError, ValueError):
        return _DEFAULT_INDEX_STALE_SECONDS


def _resolve_symbol_root() -> Path:
    configured = str(getattr(config, "SYMBOL_TABLE_RELATIVE_PATH", "symbols") or "symbols").strip()
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return path


def _os_hint(rel_path: str) -> str:
    lower = rel_path.lower().replace("\\", "/")
    if "windows" in lower:
        return "windows"
    if "macos" in lower or lower.startswith("mac/") or "/mac/" in lower:
        return "mac"
    if any(marker in lower for marker in _LINUX_MARKERS):
        return "linux"
    return "unknown"


def _config_bool(name: str, default: bool) -> bool:
    """Read a bool-like config value without treating ``\"false\"`` as true."""
    value = getattr(config, name, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _config_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(getattr(config, name, default)))
    except (TypeError, ValueError):
        return default


def _notify_progress(callback: _ProgressCallback, **event: Any) -> None:
    """Publish best-effort progress without letting UI failures stop downloads."""
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        logger.debug("Symbol progress callback failed", exc_info=True)


def _normalised_phrase(value: str) -> str:
    """Make separator changes in public ISF paths irrelevant to matching."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _contains_release(path: str, release: str) -> bool:
    """Match a complete release, not ``5.15.0-101`` inside ``...-1011``."""
    wanted = _normalised_phrase(release)
    if not wanted:
        return False
    haystack = _normalised_phrase(path)
    return f"-{wanted}-" in f"-{haystack}-"


class _RateLimitError(RuntimeError):
    def __init__(self, message: str, reset_at: int = 0, remaining: int = 0, limit: int = 0):
        super().__init__(message)
        self.reset_at = reset_at
        self.remaining = remaining
        self.limit = limit


class SymbolService:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Zero-SymbolManager/1.0",
            "Accept": "application/vnd.github+json",
        })
        token = _resolve_github_token()
        self._github_auth = bool(token)
        if token:
            # Classic PAT and fine-grained tokens both accept Bearer.
            self._session.headers["Authorization"] = f"Bearer {token}"
        self._repos = _configured_repos()
        # Per-repo cache: full_name -> state dict
        self._repo_state: dict[str, dict] = {}
        # Guards creation of per-repo entries below.
        self._registry_lock = threading.Lock()
        # One refresh lock per repo so concurrent requests share a single GitHub
        # tree call instead of each burning a slot of the 60/hr anonymous quota.
        self._repo_locks: dict[str, threading.Lock] = {}
        # requests.Session is not documented thread-safe, so parallel raw-content
        # downloads each get their own.
        self._thread_local = threading.local()
        self._rate_limit: dict[str, Any] = {
            "limit": 0,
            "remaining": -1,
            "reset_at": 0,
            "authenticated": self._github_auth,
        }
        self._local_cache_expires_at: float = 0.0
        self._local_cache_payload: Optional[dict] = None

    def list_repos(self) -> dict:
        return {
            "repos": [r.full for r in self._repos],
            "default": self._repos[0].full,
            "github_auth": self._github_auth,
            "rate_limit": dict(self._rate_limit),
        }

    @staticmethod
    def _rank_linux_candidates(
        items: list[dict], kernel: LinuxKernelInfo
    ) -> list[tuple[int, dict]]:
        """Return exact-release ISFs, with distro/architecture hints ranked first.

        An ISF's filename cannot prove that its embedded banner is identical,
        so this deliberately requires an exact *kernel release* and only
        downloads the equally best candidates.  Volatility still performs the
        final exact banner check when a plugin runs.
        """
        ranked: list[tuple[int, dict]] = []
        for item in items:
            path = str(item.get("path") or "")
            if not path:
                continue
            os_family = str(item.get("os") or _os_hint(path)).lower()
            if os_family != "linux" or not _contains_release(path, kernel.release):
                continue

            normalized_path = f"-{_normalised_phrase(path)}-"
            score = 100  # exact complete release
            if kernel.distro:
                distro_terms = _DISTRO_PATH_MARKERS.get(kernel.distro, (kernel.distro,))
                if any(
                    f"-{_normalised_phrase(term)}-" in normalized_path
                    for term in distro_terms
                ):
                    score += 30
            if kernel.architecture:
                arch_terms = _ARCH_PATH_MARKERS.get(
                    kernel.architecture, (kernel.architecture,)
                )
                if any(
                    f"-{_normalised_phrase(term)}-" in normalized_path
                    for term in arch_terms
                ):
                    score += 15
            ranked.append((score, item))

        ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("path") or "")))
        return ranked

    @staticmethod
    def _auto_scan_payload(detection: LinuxKernelDetection) -> dict:
        return {
            "bytes_scanned": detection.bytes_scanned,
            "image_size": detection.image_size,
            "scan_limit": detection.scan_limit,
            "complete": detection.complete,
            "limit_reached": detection.limit_reached,
        }

    @staticmethod
    def _auto_base_result(*, enabled: bool, status: str, **extra: Any) -> dict:
        result = {"enabled": enabled, "status": status}
        result.update(extra)
        return result

    def auto_download_for_image(
        self,
        image_path: str,
        *,
        progress_callback: _ProgressCallback = None,
    ) -> dict:
        """Detect a Linux banner and fetch the best matching missing ISF files.

        This is intentionally non-fatal: an image is still usable when a
        banner is absent, a remote index is unavailable, or no exact release is
        published. Progress events allow the Web UI to distinguish kernel
        detection, candidate matching, and byte-level symbol downloading.
        """
        if not _config_bool("AUTO_DOWNLOAD_LINUX_SYMBOLS_ON_LOAD", True):
            return self._auto_base_result(enabled=False, status="disabled")

        _notify_progress(
            progress_callback,
            stage="detecting",
            percent=None,
            message="正在识别 Linux 内核",
        )
        scan_limit = _config_int("AUTO_SYMBOL_SCAN_MAX_BYTES", 0)
        chunk_bytes = _config_int(
            "AUTO_SYMBOL_SCAN_CHUNK_BYTES",
            _AUTO_SCAN_DEFAULT_CHUNK_BYTES,
            minimum=4096,
        )
        try:
            detection = detect_linux_kernel(
                image_path,
                max_scan_bytes=scan_limit,
                chunk_bytes=chunk_bytes,
            )
        except Exception as e:
            logger.warning("Linux kernel banner scan failed for %s: %s", image_path, e)
            return self._auto_base_result(
                enabled=True,
                status="scan_failed",
                reason=str(e),
            )

        scan = self._auto_scan_payload(detection)
        kernel = detection.kernel
        if kernel is None:
            return self._auto_base_result(
                enabled=True,
                status="scan_limit_reached" if detection.limit_reached else "not_detected",
                scan=scan,
            )

        kernel_payload = kernel.as_dict()
        _notify_progress(
            progress_callback,
            stage="matching",
            percent=None,
            message=f"正在匹配内核 {kernel.release} 的符号表",
            kernel=kernel_payload,
        )
        max_candidates = min(
            50,
            _config_int("AUTO_SYMBOL_DOWNLOAD_MAX_CANDIDATES", 4, minimum=1),
        )
        index_errors: list[str] = []
        ambiguous_result: Optional[dict] = None

        # Repositories are ordered by config, so the first unambiguous exact
        # match wins.  This avoids downloading duplicate ISFs from mirrors.
        for ref in self._repos:
            try:
                self._ensure_remote_index(ref)
            except Exception as e:
                index_errors.append(f"{ref.full}: {e}")
                continue

            state = self._state(ref)
            if not state["remote_index"]:
                if state.get("last_remote_error"):
                    index_errors.append(f"{ref.full}: {state['last_remote_error']}")
                continue

            ranked = self._rank_linux_candidates(state["remote_index"], kernel)
            if not ranked:
                continue
            best_score = ranked[0][0]
            selected = [item for score, item in ranked if score == best_score]
            paths = list(dict.fromkeys(str(item["path"]) for item in selected))
            if len(paths) > max_candidates:
                ambiguous_result = self._auto_base_result(
                    enabled=True,
                    status="ambiguous",
                    kernel=kernel_payload,
                    scan=scan,
                    repo=ref.full,
                    candidate_count=len(paths),
                    candidates=paths[:max_candidates],
                    reason=(
                        f"{len(paths)} matching symbol tables exceed "
                        f"AUTO_SYMBOL_DOWNLOAD_MAX_CANDIDATES={max_candidates}"
                    ),
                )
                # Another configured repository may have fewer build variants.
                continue

            try:
                download = self.download_symbols(
                    paths,
                    repo=ref.full,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                logger.warning(
                    "Auto-download for Linux kernel %s from %s failed: %s",
                    kernel.release,
                    ref.full,
                    e,
                )
                return self._auto_base_result(
                    enabled=True,
                    status="download_failed",
                    kernel=kernel_payload,
                    scan=scan,
                    repo=ref.full,
                    candidates=paths,
                    reason=str(e),
                )

            downloaded = download.get("downloaded") or []
            skipped = download.get("skipped") or []
            failed = download.get("failed") or []
            if failed and not downloaded and not skipped:
                status = "download_failed"
            elif failed:
                status = "partial"
            elif downloaded:
                status = "downloaded"
            else:
                status = "present"
            logger.info(
                "Auto symbol lookup for %s: status=%s repo=%s downloaded=%d skipped=%d failed=%d",
                kernel.release,
                status,
                ref.full,
                len(downloaded),
                len(skipped),
                len(failed),
            )
            return self._auto_base_result(
                enabled=True,
                status=status,
                kernel=kernel_payload,
                scan=scan,
                repo=ref.full,
                candidates=paths,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                root=download.get("root", ""),
            )

        if ambiguous_result is not None:
            return ambiguous_result
        if index_errors:
            return self._auto_base_result(
                enabled=True,
                status="remote_unavailable",
                kernel=kernel_payload,
                scan=scan,
                reason="; ".join(index_errors[:3]),
            )
        return self._auto_base_result(
            enabled=True,
            status="no_match",
            kernel=kernel_payload,
            scan=scan,
        )

    def _resolve_repo(self, repo: str = "") -> _RepoRef:
        if not repo or not str(repo).strip():
            return self._repos[0]
        requested = _parse_repo(repo)
        for known in self._repos:
            if known.full.lower() == requested.full.lower():
                return known
        allowed = {r.full.lower() for r in self._repos}
        if requested.full.lower() not in allowed:
            raise ValueError(f"unsupported repo: {requested.full}")
        return requested

    def _state(self, ref: _RepoRef) -> dict:
        key = ref.full
        with self._registry_lock:
            if key not in self._repo_state:
                self._repo_state[key] = {
                    "cache_expires_at": 0.0,
                    "default_branch": "master",
                    "remote_index": [],
                    # Lowercased "name\npath" per index entry, aligned with
                    # remote_index. Kept out of the items themselves so the disk
                    # index stays half the size.
                    "search_keys": [],
                    "last_remote_error": "",
                    "last_remote_updated_at": 0,
                    "source": "",  # memory | disk | network
                }
            return self._repo_state[key]

    def _repo_lock(self, ref: _RepoRef) -> threading.Lock:
        with self._registry_lock:
            lock = self._repo_locks.get(ref.full)
            if lock is None:
                lock = threading.Lock()
                self._repo_locks[ref.full] = lock
            return lock

    def _disk_path(self, ref: _RepoRef) -> Path:
        return _DISK_INDEX_DIR / ref.disk_key

    def _note_rate_headers(self, resp: requests.Response) -> None:
        try:
            limit = int(resp.headers.get("X-RateLimit-Limit") or 0)
            remaining = int(resp.headers.get("X-RateLimit-Remaining") or -1)
            reset_at = int(resp.headers.get("X-RateLimit-Reset") or 0)
        except (TypeError, ValueError):
            return
        if limit or remaining >= 0 or reset_at:
            self._rate_limit.update({
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_at,
                "authenticated": self._github_auth,
            })

    def _raise_for_github(self, resp: requests.Response, context: str) -> None:
        self._note_rate_headers(resp)
        if resp.status_code in (403, 429):
            remaining = int(resp.headers.get("X-RateLimit-Remaining") or 0)
            reset_at = int(resp.headers.get("X-RateLimit-Reset") or 0)
            limit = int(resp.headers.get("X-RateLimit-Limit") or 0)
            # GitHub secondary rate limit / abuse also uses 403; prefer rate-limit message when remaining is 0.
            body = ""
            try:
                body = str((resp.json() or {}).get("message") or "")
            except Exception:
                body = (resp.text or "")[:200]
            msg = body or f"GitHub API rate limited during {context}"
            if remaining == 0 or "rate limit" in msg.lower():
                when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(reset_at)) if reset_at else "unknown"
                hint = (
                    " 未认证配额约 60 次/小时；可在 zero/config.py 设置 SYMBOL_GITHUB_TOKEN "
                    "或环境变量 GITHUB_TOKEN / ZERO_GITHUB_TOKEN 提升至约 5000 次/小时。"
                    if not self._github_auth
                    else " 已配置 token 仍被限流，请稍后再试。"
                )
                raise _RateLimitError(
                    f"GitHub API 配额用尽（reset≈{when}）。{hint} 详情: {msg}",
                    reset_at=reset_at,
                    remaining=remaining,
                    limit=limit,
                )
        resp.raise_for_status()

    def _github_get(self, url: str, *, timeout: int = 30, context: str = "request") -> requests.Response:
        # If we already know remaining==0 and reset is in the future, fail fast.
        remaining = self._rate_limit.get("remaining")
        reset_at = int(self._rate_limit.get("reset_at") or 0)
        if remaining == 0 and reset_at and time.time() < reset_at:
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(reset_at))
            raise _RateLimitError(
                f"GitHub API 配额已用尽，约 {when} 后恢复。",
                reset_at=reset_at,
                remaining=0,
                limit=int(self._rate_limit.get("limit") or 0),
            )
        resp = self._session.get(url, timeout=timeout)
        self._raise_for_github(resp, context)
        return resp

    def _load_disk_index(self, ref: _RepoRef) -> Optional[dict]:
        path = self._disk_path(ref)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read disk symbol index %s: %s", path, e)
            return None
        items = data.get("items")
        if not isinstance(items, list) or not items:
            return None
        updated_at = int(data.get("updated_at") or 0)
        age = time.time() - updated_at if updated_at else 10**12
        if age > _index_stale_seconds():
            logger.info("Disk symbol index for %s is beyond stale window (age=%ss)", ref.full, int(age))
            # Still return it as last-resort payload; caller decides.
        return {
            "items": items,
            "default_branch": str(data.get("default_branch") or "master"),
            "updated_at": updated_at,
            "age": age,
        }

    def _save_disk_index(self, ref: _RepoRef, items: list[dict], branch: str, updated_at: int) -> None:
        path = self._disk_path(ref)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "repo": ref.full,
                "default_branch": branch,
                "updated_at": updated_at,
                "count": len(items),
                "items": items,
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.warning("Failed to write disk symbol index %s: %s", path, e)

    def _apply_index(
        self,
        ref: _RepoRef,
        items: list[dict],
        branch: str,
        updated_at: int,
        *,
        source: str,
        error: str = "",
        ttl: Optional[int] = None,
    ) -> None:
        state = self._state(ref)
        state["remote_index"] = items
        state["search_keys"] = [
            f"{item['name']}\n{item['path']}".lower() for item in items
        ]
        state["default_branch"] = branch or state.get("default_branch") or "master"
        state["last_remote_updated_at"] = int(updated_at or 0)
        state["last_remote_error"] = error
        state["source"] = source
        soft = _index_ttl_seconds() if ttl is None else max(30, int(ttl))
        # Fresh network data uses soft TTL; error/stale uses shorter retry interval when empty,
        # otherwise keep serving until soft TTL while still flagging the error.
        state["cache_expires_at"] = time.time() + soft

    def _seed_from_disk(self, ref: _RepoRef) -> bool:
        """Load disk index into memory if present. Returns True when memory has data."""
        state = self._state(ref)
        if state["remote_index"]:
            return True
        disk = self._load_disk_index(ref)
        if not disk:
            return False
        age = float(disk["age"])
        soft = _index_ttl_seconds()
        # Remaining soft TTL from disk age, at least 60s so we don't thrash.
        remaining_soft = max(60, int(soft - age)) if age < soft else 60
        self._apply_index(
            ref,
            disk["items"],
            disk["default_branch"],
            disk["updated_at"],
            source="disk",
            error="",
            ttl=remaining_soft if age < soft else 60,
        )
        return True

    def _candidate_branches(self, ref: _RepoRef) -> list[str]:
        state = self._state(ref)
        candidates: list[str] = []
        for b in (state.get("default_branch"), "master", "main"):
            if b and b not in candidates:
                candidates.append(str(b))
        return candidates

    def _build_index_via_tree(self, ref: _RepoRef, branch: str) -> list[dict] | None:
        """Recursive git/trees — typically 1 API call. Returns None if truncated."""
        encoded_branch = requests.utils.quote(branch, safe="")
        tree_url = (
            f"{_API_BASE}/repos/{ref.owner}/{ref.name}"
            f"/git/trees/{encoded_branch}?recursive=1"
        )
        resp = self._github_get(tree_url, timeout=45, context=f"tree {ref.full}@{branch}")
        data = resp.json() or {}
        if data.get("truncated"):
            logger.warning(
                "Git tree response truncated for %s@%s; using partial tree (avoid Contents API rate burn)",
                ref.full,
                branch,
            )
        items: list[dict] = []
        for node in (data.get("tree") or []):
            if node.get("type") != "blob":
                continue
            rel_path = str(node.get("path") or "")
            if not rel_path or not rel_path.endswith(_ALLOWED_SUFFIXES):
                continue
            items.append(self._make_item(rel_path, int(node.get("size") or 0), ref.full))
        items.sort(key=lambda x: x["path"])
        return items

    def _build_remote_index(self, ref: _RepoRef) -> tuple[list[dict], str]:
        """
        Build index with minimal API calls.
        Prefer recursive tree on known branch (1 call). No separate /repos metadata call.
        """
        last_err: Optional[Exception] = None
        for branch in self._candidate_branches(ref):
            try:
                items = self._build_index_via_tree(ref, branch)
                if items is not None:
                    return items, branch
            except _RateLimitError:
                raise
            except requests.HTTPError as e:
                last_err = e
                status = e.response.status_code if e.response is not None else 0
                if status == 404:
                    continue
                raise
            except Exception as e:
                last_err = e
                logger.warning("Tree fetch failed for %s@%s: %s", ref.full, branch, e)
        if last_err:
            raise last_err
        raise RuntimeError(f"Unable to build symbol index for {ref.full}")

    @staticmethod
    def _make_item(rel_path: str, size: int, repo: str) -> dict:
        name = rel_path.rsplit("/", 1)[-1]
        return {
            "path": rel_path,
            "name": name,
            "size": size,
            "os": _os_hint(rel_path),
            "repo": repo,
        }

    def _ensure_remote_index(self, ref: _RepoRef, *, force: bool = False) -> None:
        state = self._state(ref)
        # Fast path: fresh memory index needs no lock at all.
        if not force and state["remote_index"] and time.time() < state["cache_expires_at"]:
            return
        # Serialize refreshes per repo; whoever gets in first does the single
        # network call and the rest fall through the re-check below.
        with self._repo_lock(ref):
            self._refresh_remote_index_locked(ref, force=force)

    def _refresh_remote_index_locked(self, ref: _RepoRef, *, force: bool = False) -> None:
        state = self._state(ref)
        now = time.time()

        # Warm from disk before deciding to hit the network.
        self._seed_from_disk(ref)

        if not force and state["remote_index"] and now < state["cache_expires_at"]:
            return

        # Soft TTL expired: try refresh. On failure, keep stale data within hard window.
        try:
            items, branch = self._build_remote_index(ref)
            updated_at = int(now)
            self._apply_index(ref, items, branch, updated_at, source="network", error="")
            self._save_disk_index(ref, items, branch, updated_at)
            logger.info(
                "Refreshed symbol index for %s: %d files (auth=%s, remaining=%s)",
                ref.full,
                len(items),
                self._github_auth,
                self._rate_limit.get("remaining"),
            )
        except Exception as e:
            err = str(e)
            state["last_remote_error"] = err
            logger.warning("Failed to refresh remote symbol index for %s: %s", ref.full, e)

            if state["remote_index"]:
                # Keep serving stale; re-check network after a short backoff on rate limit.
                backoff = 300 if isinstance(e, _RateLimitError) else 120
                state["cache_expires_at"] = now + backoff
                state["source"] = state.get("source") or "stale"
                return

            # No memory data: try disk one more time as last resort.
            disk = self._load_disk_index(ref)
            if disk:
                self._apply_index(
                    ref,
                    disk["items"],
                    disk["default_branch"],
                    disk["updated_at"],
                    source="disk-stale",
                    error=err,
                    ttl=300 if isinstance(e, _RateLimitError) else 120,
                )
                return

            # Empty — short retry delay so we don't hammer the API.
            state["cache_expires_at"] = now + (300 if isinstance(e, _RateLimitError) else 30)
            state["source"] = ""

    def list_remote_symbols(
        self,
        query: str = "",
        os_family: str = "",
        page: int = 1,
        page_size: int = 100,
        repo: str = "",
        force_refresh: bool = False,
    ) -> dict:
        ref = self._resolve_repo(repo)
        self._ensure_remote_index(ref, force=force_refresh)
        state = self._state(ref)

        q = (query or "").strip().lower()
        os_filter = (os_family or "").strip().lower()
        if os_filter in {"macos", "osx", "darwin"}:
            os_filter = "mac"

        filtered = state["remote_index"]
        if q:
            # Match against the precomputed lowercase keys instead of lowering
            # every name/path on every request (the index can hold 100k+ entries).
            keys = state["search_keys"]
            filtered = [
                item for item, key in zip(filtered, keys, strict=True) if q in key
            ]
        if os_filter in {"windows", "linux", "mac"}:
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
            "total_pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "default_branch": state["default_branch"],
            "repo": ref.full,
            "repos": [r.full for r in self._repos],
            "status": {
                "ok": not bool(state["last_remote_error"]) and bool(state["remote_index"]),
                "error": state["last_remote_error"],
                "last_updated_at": state["last_remote_updated_at"],
                "cached": bool(state["remote_index"]),
                "source": state.get("source") or "",
                "github_auth": self._github_auth,
                "rate_limit": dict(self._rate_limit),
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
            st = p.stat()
            items.append({
                "path": rel,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
        payload = {"root": str(root), "items": items, "total": len(items)}
        self._local_cache_payload = payload
        self._local_cache_expires_at = now + _LOCAL_INDEX_TTL_SECONDS
        return payload

    def _raw_session(self) -> requests.Session:
        """Per-thread session for symbol-content downloads.

        No Authorization header: direct raw downloads and optional gh-proxy
        downloads are public, and this keeps the GitHub API token off both
        content hosts.
        """
        session = getattr(self._thread_local, "raw_session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": "Zero-SymbolManager/1.0"})
            self._thread_local.raw_session = session
        return session

    @staticmethod
    def _symbol_download_url(
        ref: _RepoRef,
        branch: str,
        rel: str,
        *,
        use_gh_proxy: bool = False,
    ) -> str:
        """Return the direct raw URL or its gh-proxy.com equivalent."""
        raw_url = f"{ref.raw_base}/{branch}/{rel}"
        if use_gh_proxy:
            # gh-proxy.com accepts a complete GitHub/raw URL as the path.
            return f"{_GH_PROXY_BASE}{raw_url}"
        return raw_url

    def _fetch_one_symbol(
        self,
        ref: _RepoRef,
        branch: str,
        rel: str,
        target: Path,
        *,
        use_gh_proxy: bool = False,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> dict:
        """Stream one file to disk. Returns a result record for the caller to bucket."""
        url = self._symbol_download_url(
            ref,
            branch,
            rel,
            use_gh_proxy=use_gh_proxy,
        )
        tmp = target.with_name(target.name + ".part")
        try:
            # Direct raw-content requests are separate from the REST API core
            # quota; optional proxy requests are also streamed so a large ISF
            # never has to fit in memory alongside the response object.
            with self._raw_session().get(url, timeout=120, stream=True) as resp:
                resp.raise_for_status()
                size = 0
                content_length = resp.headers.get("content-length")
                try:
                    total_size = max(0, int(content_length)) if content_length else None
                except (TypeError, ValueError):
                    total_size = None
                if progress_callback is not None:
                    progress_callback(0, total_size)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                        if chunk:
                            fh.write(chunk)
                            size += len(chunk)
                            if progress_callback is not None:
                                progress_callback(size, total_size)
            tmp.replace(target)
            return {"bucket": "downloaded", "path": rel, "size": size, "repo": ref.full}
        except Exception as e:
            tmp.unlink(missing_ok=True)
            logger.warning("Failed to download symbol %s from %s: %s", rel, ref.full, e)
            return {"bucket": "failed", "path": rel, "reason": str(e)}

    def download_symbols(
        self,
        paths: list[str],
        repo: str = "",
        *,
        use_gh_proxy: bool = False,
        progress_callback: _ProgressCallback = None,
    ) -> dict:
        """Download from GitHub raw directly or through the fixed gh-proxy endpoint."""
        use_gh_proxy = bool(use_gh_proxy)
        ref = self._resolve_repo(repo)
        self._ensure_remote_index(ref)
        state = self._state(ref)
        allowed_paths = {item["path"] for item in state["remote_index"]}
        branch = state["default_branch"]

        root = _resolve_symbol_root()
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()

        downloaded: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        pending: list[tuple[str, Path]] = []

        for rel_path in paths:
            rel = str(rel_path or "").strip().lstrip("/")
            if not rel:
                continue
            if rel not in allowed_paths:
                failed.append({"path": rel, "reason": "not_found_in_remote_index"})
                continue
            target = (root / rel).resolve()
            if not target.is_relative_to(resolved_root):
                failed.append({"path": rel, "reason": "invalid_target_path"})
                continue
            if target.exists():
                skipped.append({"path": rel, "reason": "exists"})
                continue
            pending.append((rel, target))

        if pending:
            progress_lock = threading.Lock()
            file_progress = {
                rel: {"downloaded": 0, "total": None, "complete": False}
                for rel, _target in pending
            }

            def publish_download_progress(current_file: str = "") -> None:
                with progress_lock:
                    states = list(file_progress.values())
                    completed_files = sum(1 for item in states if item["complete"])
                    all_totals_known = all(item["total"] is not None for item in states)
                    downloaded_bytes = sum(int(item["downloaded"] or 0) for item in states)
                    total_bytes = (
                        sum(int(item["total"] or 0) for item in states)
                        if all_totals_known
                        else None
                    )
                    if total_bytes:
                        percent = min(100.0, downloaded_bytes * 100.0 / total_bytes)
                    elif completed_files:
                        percent = completed_files * 100.0 / len(states)
                    else:
                        percent = None
                    event = {
                        "stage": "downloading",
                        "percent": round(percent, 1) if percent is not None else None,
                        "message": f"正在下载 {len(states)} 个匹配符号表",
                        "downloaded_bytes": downloaded_bytes,
                        "total_bytes": total_bytes,
                        "completed_files": completed_files,
                        "total_files": len(states),
                        "current_file": current_file,
                    }
                _notify_progress(progress_callback, **event)

            def fetch_pending(item: tuple[str, Path]) -> dict:
                rel, target = item

                def on_bytes(downloaded_bytes: int, total_bytes: Optional[int]) -> None:
                    with progress_lock:
                        state = file_progress[rel]
                        state["downloaded"] = downloaded_bytes
                        state["total"] = total_bytes
                    publish_download_progress(rel)

                record = self._fetch_one_symbol(
                    ref,
                    branch,
                    rel,
                    target,
                    use_gh_proxy=use_gh_proxy,
                    progress_callback=on_bytes,
                )
                with progress_lock:
                    state = file_progress[rel]
                    state["complete"] = True
                    if record.get("bucket") == "downloaded":
                        size = int(record.get("size") or state["downloaded"] or 0)
                        state["downloaded"] = size
                        if state["total"] is None:
                            state["total"] = size
                publish_download_progress(rel)
                return record

            publish_download_progress()
            workers = min(_DOWNLOAD_WORKERS, len(pending))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = pool.map(fetch_pending, pending)
                for record in results:
                    bucket = record.pop("bucket")
                    (downloaded if bucket == "downloaded" else failed).append(record)

        self._local_cache_payload = None
        self._local_cache_expires_at = 0.0

        return {
            "downloaded": downloaded,
            "skipped": skipped,
            "failed": failed,
            "root": str(root),
            "repo": ref.full,
            "use_gh_proxy": use_gh_proxy,
        }


_symbol_service: Optional[SymbolService] = None


def get_symbol_service() -> SymbolService:
    global _symbol_service
    if _symbol_service is None:
        _symbol_service = SymbolService()
    return _symbol_service
