"""Small, dependency-free Linux kernel-banner detector.

The normal Volatility Linux stack needs a matching ISF symbol table before it
can build a layer.  A Linux ``linux_banner`` string is present in physical
memory, however, so it can be found without starting Volatility or loading any
symbols.  This module intentionally only streams the image and stops at the
first valid banner; it is therefore suitable for the image-load path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_PREFIX = b"Linux version "
_MAX_BANNER_BYTES = 4096
_DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024
_MIN_CHUNK_BYTES = 1
_RELEASE_RE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?[A-Za-z0-9._+~:-]*$")

_DISTRO_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ubuntu", ("ubuntu",)),
    ("debian", ("debian",)),
    ("kali", ("kali",)),
    ("almalinux", ("almalinux",)),
    ("rocky", ("rocky", "rocky linux")),
    ("centos", ("centos",)),
    ("rhel", ("red hat", "redhat", "rhel")),
    ("fedora", ("fedora",)),
    ("oracle", ("oracle",)),
    ("amazon", ("amazon", "amzn")),
    ("suse", ("suse", "opensuse")),
    ("arch", ("arch linux",)),
)

_ARCH_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x86_64", ("x86_64", "amd64")),
    ("aarch64", ("aarch64", "arm64")),
    ("arm", ("armv7", "armv6", " arm ")),
    ("ppc64le", ("ppc64le",)),
    ("s390x", ("s390x",)),
    ("i386", ("i386", "i486", "i586", "i686")),
)


@dataclass(frozen=True)
class LinuxKernelInfo:
    """Kernel details recovered from a ``Linux version`` banner."""

    banner: str
    release: str
    offset: int
    distro: str = ""
    architecture: str = ""

    def as_dict(self) -> dict:
        return {
            "banner": self.banner,
            "release": self.release,
            "offset": self.offset,
            "distro": self.distro,
            "architecture": self.architecture,
        }


@dataclass(frozen=True)
class LinuxKernelDetection:
    """Result of a bounded streaming banner scan."""

    kernel: Optional[LinuxKernelInfo]
    bytes_scanned: int
    image_size: int
    scan_limit: int

    @property
    def complete(self) -> bool:
        """Whether the whole image was scanned (rather than a configured cap)."""
        return self.bytes_scanned >= self.image_size

    @property
    def limit_reached(self) -> bool:
        return (
            self.kernel is None
            and self.scan_limit > 0
            and self.bytes_scanned >= self.scan_limit
            and self.bytes_scanned < self.image_size
        )

    def as_dict(self) -> dict:
        return {
            "found": self.kernel is not None,
            "kernel": self.kernel.as_dict() if self.kernel else None,
            "bytes_scanned": self.bytes_scanned,
            "image_size": self.image_size,
            "scan_limit": self.scan_limit,
            "complete": self.complete,
            "limit_reached": self.limit_reached,
        }


def _detect_distro(banner: str) -> str:
    lower = banner.lower()
    for distro, markers in _DISTRO_MARKERS:
        if any(marker in lower for marker in markers):
            return distro
    return ""


def _detect_architecture(banner: str) -> str:
    lower = f" {banner.lower()} "
    for architecture, markers in _ARCH_MARKERS:
        if any(marker in lower for marker in markers):
            return architecture
    return ""


def _parse_banner(raw: bytes, offset: int) -> Optional[LinuxKernelInfo]:
    """Validate and parse one candidate banner.

    ``linux_banner`` is an ASCII, NUL-terminated string.  Requiring printable
    bytes prevents arbitrary binary data containing the prefix from becoming a
    false kernel match.
    """
    if not raw.startswith(_PREFIX) or len(raw) <= len(_PREFIX):
        return None
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        return None

    banner = raw.decode("ascii", errors="strict").strip()
    parts = banner.split(maxsplit=2)
    # ``Linux version <release> ...``; a release alone is still useful and
    # occurs in truncated captures, so do not require the compiler suffix.
    if len(parts) < 3 or parts[0] != "Linux" or parts[1] != "version":
        return None
    release = parts[2].split(maxsplit=1)[0]
    if not _RELEASE_RE.fullmatch(release):
        return None

    return LinuxKernelInfo(
        banner=banner,
        release=release,
        offset=offset,
        distro=_detect_distro(banner),
        architecture=_detect_architecture(banner),
    )


def _candidate_bytes(data: bytes, start: int, *, at_end: bool) -> Optional[bytes]:
    """Return a complete candidate or ``None`` when it needs the next chunk."""
    candidate = data[start : start + _MAX_BANNER_BYTES]
    for index, byte in enumerate(candidate):
        if byte in (0, 10, 13):
            return candidate[:index]
        if byte < 0x20 or byte > 0x7E:
            # Preserve the offending byte so _parse_banner rejects this
            # candidate instead of accepting its valid-looking prefix.
            return candidate[: index + 1]

    # A banner spanning the current chunk is retained in the overlap and tried
    # again after the next read.  At EOF/cap, accept the bounded final string.
    if len(candidate) >= _MAX_BANNER_BYTES:
        # A real linux_banner is far shorter than this.  Do not treat a huge
        # printable region in arbitrary memory as a valid banner.
        return b""
    if at_end:
        return candidate
    return None


def detect_linux_kernel(
    image_path: str | Path,
    *,
    max_scan_bytes: int = 0,
    chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
) -> LinuxKernelDetection:
    """Find the first valid Linux banner with bounded, constant-size reads.

    ``max_scan_bytes=0`` scans until a banner is found or the end of the image.
    A positive cap is useful for deployments that prefer a fast best-effort
    load over a full scan of an image that is not Linux.
    """
    path = Path(image_path).expanduser()
    image_size = path.stat().st_size
    try:
        requested_limit = max(0, int(max_scan_bytes))
    except (TypeError, ValueError):
        requested_limit = 0
    scan_limit = min(image_size, requested_limit) if requested_limit else image_size
    try:
        read_size = max(_MIN_CHUNK_BYTES, int(chunk_bytes))
    except (TypeError, ValueError):
        read_size = _DEFAULT_CHUNK_BYTES

    bytes_scanned = 0
    tail = b""
    seen_offsets: set[int] = set()

    with path.open("rb") as image_file:
        while bytes_scanned < scan_limit:
            remaining = scan_limit - bytes_scanned
            chunk = image_file.read(min(read_size, remaining))
            if not chunk:
                break

            buffer_start = bytes_scanned - len(tail)
            data = tail + chunk
            at_end = bytes_scanned + len(chunk) >= scan_limit
            index = 0
            while True:
                index = data.find(_PREFIX, index)
                if index < 0:
                    break
                absolute_offset = buffer_start + index
                index += len(_PREFIX)
                if absolute_offset in seen_offsets:
                    continue

                raw = _candidate_bytes(
                    data, absolute_offset - buffer_start, at_end=at_end
                )
                if raw is None:
                    # The prefix is in the overlap but the terminating byte is
                    # in the next read.  Do not mark it seen yet.
                    continue
                seen_offsets.add(absolute_offset)
                kernel = _parse_banner(raw, absolute_offset)
                if kernel is not None:
                    return LinuxKernelDetection(
                        kernel=kernel,
                        bytes_scanned=bytes_scanned + len(chunk),
                        image_size=image_size,
                        scan_limit=requested_limit,
                    )

            bytes_scanned += len(chunk)
            # Keep enough bytes to re-evaluate a prefix or an incomplete banner
            # that crossed the chunk boundary, without retaining image-scale data.
            tail = data[-_MAX_BANNER_BYTES:]
            # A completed candidate can only reappear while it remains in the
            # overlap.  Prune older offsets so an adversarial/garbled image
            # containing many prefix strings cannot grow this set with image
            # size.
            tail_start = bytes_scanned - len(tail)
            seen_offsets = {offset for offset in seen_offsets if offset >= tail_start}

    return LinuxKernelDetection(
        kernel=None,
        bytes_scanned=bytes_scanned,
        image_size=image_size,
        scan_limit=requested_limit,
    )
