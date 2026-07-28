"""Rule centre and marketplace APIs for YARA-X."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from zero.yarax.errors import ConflictError, NotFoundError, ValidationError, YaraXError
from zero.yarax.market import get_market_service
from zero.yarax.store import get_yarax_store
from zero.yarax.zipio import MAX_ZIP_BYTES

router = APIRouter(prefix="/api/yarax", tags=["YARA-X"])


def _raise_http(exc: Exception):
    if isinstance(exc, ConflictError):
        raise HTTPException(409, {"code": exc.code, "message": str(exc)})
    if isinstance(exc, NotFoundError):
        raise HTTPException(404, {"code": exc.code, "message": str(exc)})
    if isinstance(exc, ValidationError):
        raise HTTPException(400, {
            "code": exc.code, "message": str(exc), "diagnostics": exc.diagnostics,
        })
    raise exc


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    package_id: Optional[str] = None
    description: str = ""
    manifest: dict[str, Any] = Field(default_factory=dict)


class PackageState(BaseModel):
    enabled: bool


class DraftWrite(BaseModel):
    path: str
    content: str
    base_revision: int
    file_sha: Optional[str] = None


class DraftRename(BaseModel):
    old_path: str
    new_path: str
    base_revision: int


class DraftDelete(BaseModel):
    path: str
    base_revision: int


class ValidateRequest(BaseModel):
    manifest: Optional[dict[str, Any]] = None
    relaxed_regex: bool = False


class CommitRequest(ValidateRequest):
    base_revision: Optional[int] = None


class PreviewConfirm(BaseModel):
    token: str
    package_id: Optional[str] = None
    entrypoints: Optional[list[str]] = None
    save_as_draft: bool = False


class ForkRequest(BaseModel):
    name: Optional[str] = None


class MarketSourceCreate(BaseModel):
    repository: str
    name: str = ""
    ref: str = "main"
    subdirectory: str = ""


class MarketInstall(BaseModel):
    package_id: Optional[str] = None


@router.get("/packages")
def list_packages():
    return {"packages": get_yarax_store().list_packages()}


@router.post("/packages", status_code=201)
def create_package(request: PackageCreate):
    try:
        return get_yarax_store().create_package(
            name=request.name, package_id=request.package_id,
            description=request.description, manifest=request.manifest,
        )
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}")
def get_package(package_id: str):
    try:
        return get_yarax_store().get_package(package_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.patch("/packages/{package_id}")
def update_package_state(package_id: str, request: PackageState):
    try:
        return get_yarax_store().set_enabled(package_id, request.enabled)
    except YaraXError as exc:
        _raise_http(exc)


@router.delete("/packages/{package_id}")
def delete_package(package_id: str):
    try:
        get_yarax_store().delete_package(package_id)
        return {"ok": True}
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}/export")
def export_package(package_id: str):
    try:
        path = get_yarax_store().export(package_id)
        return FileResponse(path, filename=path.name, media_type="application/zip")
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/import/preview")
async def preview_zip(
    file: UploadFile = File(...),
    entrypoint: Optional[list[str]] = Query(None),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".zip":
        raise HTTPException(400, "Only .zip archives are accepted")
    fd, tmp_name = tempfile.mkstemp(prefix="zero-yarax-", suffix=".zip")
    total = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ZIP_BYTES:
                    raise HTTPException(413, "ZIP exceeds the 50 MiB limit")
                handle.write(chunk)
        try:
            return get_yarax_store().preview_zip(Path(tmp_name), entrypoints=entrypoint)
        except YaraXError as exc:
            _raise_http(exc)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
        await file.close()


@router.post("/import/confirm", status_code=201)
def confirm_zip(request: PreviewConfirm):
    try:
        return get_yarax_store().confirm_preview(
            request.token, package_id=request.package_id,
            entrypoints=request.entrypoints, save_as_draft=request.save_as_draft,
        )
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/draft")
def create_or_get_draft(package_id: str):
    try:
        store = get_yarax_store()
        package = store.get_package(package_id)
        if package["source_type"] == "market":
            fork = store.fork_package(package_id)
            draft = store.ensure_draft(fork["id"])
            draft["forked_package_id"] = fork["id"]
            draft["forked_from"] = package_id
            return draft
        return store.ensure_draft(package_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}/draft")
def get_draft(package_id: str):
    try:
        return get_yarax_store().get_draft(package_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}/draft/file")
def read_draft_file(package_id: str, path: str = Query(...)):
    try:
        return get_yarax_store().read_draft_file(package_id, path)
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}/draft/search")
def search_draft(package_id: str, q: str = Query(..., min_length=1)):
    try:
        return {"results": get_yarax_store().search_draft(package_id, q)}
    except YaraXError as exc:
        _raise_http(exc)


@router.put("/packages/{package_id}/draft/file")
def write_draft_file(package_id: str, request: DraftWrite):
    try:
        return get_yarax_store().write_draft_file(
            package_id, request.path, request.content,
            base_revision=request.base_revision, file_sha=request.file_sha,
        )
    except YaraXError as exc:
        _raise_http(exc)


@router.delete("/packages/{package_id}/draft/file")
def delete_draft_file(package_id: str, request: DraftDelete):
    try:
        return get_yarax_store().delete_draft_file(
            package_id, request.path, request.base_revision,
        )
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/draft/rename")
def rename_draft_file(package_id: str, request: DraftRename):
    try:
        return get_yarax_store().rename_draft_file(
            package_id, request.old_path, request.new_path, request.base_revision,
        )
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/validate")
def validate_package(package_id: str, request: ValidateRequest = ValidateRequest()):
    try:
        return get_yarax_store().validate_draft(
            package_id, request.manifest, request.relaxed_regex,
        )
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/commit", status_code=201)
def commit_package(package_id: str, request: CommitRequest = CommitRequest()):
    try:
        return get_yarax_store().commit_draft(
            package_id, base_revision=request.base_revision,
            manifest_override=request.manifest, relaxed_regex=request.relaxed_regex,
        )
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}/versions")
def version_history(package_id: str):
    try:
        return {"versions": get_yarax_store().list_versions(package_id)}
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}/versions/{version_id}/diff")
def version_diff(
    package_id: str, version_id: str, other_version_id: Optional[str] = Query(None)
):
    try:
        return get_yarax_store().version_diff(package_id, version_id, other_version_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/versions/{version_id}/restore", status_code=201)
def restore_version(package_id: str, version_id: str):
    try:
        return get_yarax_store().restore_version(package_id, version_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/versions/{version_id}/rollback")
def rollback_version(package_id: str, version_id: str):
    try:
        return get_yarax_store().rollback(package_id, version_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/fork", status_code=201)
def fork_package(package_id: str, request: ForkRequest = ForkRequest()):
    try:
        return get_yarax_store().fork_package(package_id, request.name)
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/market")
def list_market():
    return {"sources": get_market_service().list_sources()}


@router.post("/market/sources", status_code=201)
def add_market_source(request: MarketSourceCreate):
    try:
        return get_market_service().add_source(
            request.repository, name=request.name, ref=request.ref,
            subdirectory=request.subdirectory,
        )
    except (YaraXError, ValueError) as exc:
        _raise_http(exc)


@router.delete("/market/sources/{source_id}")
def remove_market_source(source_id: str):
    try:
        get_market_service().delete_source(source_id)
        return {"ok": True}
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/market/{source_id}")
def inspect_market_source(source_id: str, refresh: bool = Query(False)):
    try:
        return get_market_service().inspect_source(source_id, force=refresh)
    except (YaraXError, OSError) as exc:
        _raise_http(exc)


@router.post("/market/{source_id}/install", status_code=201)
def install_market_source(source_id: str, request: MarketInstall = MarketInstall()):
    try:
        return get_market_service().install(source_id, package_id=request.package_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.get("/packages/{package_id}/updates")
def check_market_update(package_id: str):
    try:
        return get_market_service().check_update(package_id)
    except YaraXError as exc:
        _raise_http(exc)


@router.post("/packages/{package_id}/upgrade", status_code=201)
def upgrade_market_package(package_id: str):
    try:
        return get_market_service().upgrade(package_id)
    except YaraXError as exc:
        _raise_http(exc)
