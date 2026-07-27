"""Allowlisted system runtime settings API."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.backend.services.settings_service import (
    save_runtime_settings,
    settings_payload,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SaveRuntimeSettingsRequest(BaseModel):
    settings: dict[str, Any]


@router.get("")
def get_runtime_settings():
    return settings_payload()


@router.put("")
def update_runtime_settings(req: SaveRuntimeSettingsRequest):
    try:
        saved = save_runtime_settings(req.settings)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    payload = settings_payload()
    payload["settings"] = saved
    payload["updated"] = sorted(req.settings)
    return payload
