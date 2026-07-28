"""Hardened ZIP import/export for YARA-X packages."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from .compiler import normalize_relative_path
from .errors import ValidationError

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_FILES = 5000
MAX_COMPRESSION_RATIO = 100


def safe_extract_zip(
    archive: Path,
    destination: Path,
    *,
    max_zip_bytes: int = MAX_ZIP_BYTES,
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES,
    max_files: int = MAX_FILES,
    max_ratio: int = MAX_COMPRESSION_RATIO,
) -> Path:
    archive = Path(archive)
    if archive.stat().st_size > max_zip_bytes:
        raise ValidationError("ZIP exceeds the 50 MiB compressed-size limit")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    files = 0
    extracted = 0
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        for info in entries:
            if info.flag_bits & 0x1:
                raise ValidationError(f"Encrypted ZIP entry is not allowed: {info.filename}")
            raw_name = unicodedata.normalize("NFC", info.filename).replace("\\", "/")
            if raw_name.endswith("/"):
                raw_name = raw_name.rstrip("/")
                if not raw_name:
                    continue
            try:
                name = normalize_relative_path(raw_name)
            except ValidationError:
                raise ValidationError(f"Unsafe ZIP entry: {info.filename!r}")
            normalized_key = unicodedata.normalize("NFC", name).casefold()
            if normalized_key in seen:
                raise ValidationError(f"Duplicate normalized ZIP path: {name!r}")
            seen.add(normalized_key)
            mode = (info.external_attr >> 16) & 0xFFFF
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValidationError(f"Special ZIP entry is not allowed: {name!r}")
            if stat.S_ISLNK(mode):
                raise ValidationError(f"Symbolic link ZIP entry is not allowed: {name!r}")
            if info.is_dir():
                continue
            files += 1
            extracted += info.file_size
            if files > max_files:
                raise ValidationError("ZIP exceeds the 5000-file limit")
            if extracted > max_extracted_bytes:
                raise ValidationError("ZIP exceeds the 200 MiB extracted-size limit")
            if info.file_size and (
                info.compress_size == 0 or info.file_size / info.compress_size > max_ratio
            ):
                raise ValidationError(f"ZIP entry exceeds the {max_ratio}:1 compression ratio")
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)

    children = list(destination.iterdir())
    if len(children) == 1 and children[0].is_dir():
        top = children[0]
        stripped = destination.parent / (destination.name + "-stripped")
        os.replace(top, stripped)
        shutil.rmtree(destination, ignore_errors=True)
        os.replace(stripped, destination)
    return destination


def export_package(root: Path, output: Path) -> Path:
    root = Path(root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_name, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    zf.write(path, path.relative_to(root).as_posix())
        os.replace(tmp_name, output)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return output
