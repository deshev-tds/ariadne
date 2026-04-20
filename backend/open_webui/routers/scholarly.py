import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_admin_user
from open_webui.utils.scholarly_sources import (
    build_scholarly_source_rows,
    merge_scholarly_source_settings,
    normalize_scholarly_source_settings,
    probe_scholarly_source,
)


log = logging.getLogger(__name__)
router = APIRouter()


class ScholarlySourceSettingsForm(BaseModel):
    enabled: Optional[bool] = None
    api_key: Optional[str] = None


class ScholarlyConfigPayload(BaseModel):
    sources: Optional[dict[str, ScholarlySourceSettingsForm]] = None


class ScholarlyConfigForm(BaseModel):
    scholarly: Optional[ScholarlyConfigPayload] = None


class ScholarlyTestForm(BaseModel):
    source_id: str
    settings_override: Optional[ScholarlySourceSettingsForm] = None


def _scholarly_config_payload(request: Request) -> dict:
    settings = normalize_scholarly_source_settings(
        request.app.state.config.SCHOLARLY_API_SOURCES
    )
    return {
        "settings": settings,
        "sources": build_scholarly_source_rows(settings),
    }


@router.get("/config")
async def get_scholarly_config(request: Request, user=Depends(get_admin_user)):
    return {"status": True, "scholarly": _scholarly_config_payload(request)}


@router.post("/config/update")
async def update_scholarly_config(
    request: Request,
    form_data: ScholarlyConfigForm,
    user=Depends(get_admin_user),
):
    try:
        updates = {
            source_id: payload.model_dump(exclude_none=True)
            for source_id, payload in (form_data.scholarly or ScholarlyConfigPayload()).sources.items()
        } if (form_data.scholarly and form_data.scholarly.sources) else {}

        request.app.state.config.SCHOLARLY_API_SOURCES = merge_scholarly_source_settings(
            request.app.state.config.SCHOLARLY_API_SOURCES,
            updates,
            configured_by_email=getattr(user, "email", None),
        )

        return {"status": True, "scholarly": _scholarly_config_payload(request)}
    except Exception as e:
        log.exception("Failed to update scholarly source config")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/test")
async def test_scholarly_source(
    request: Request,
    form_data: ScholarlyTestForm,
    user=Depends(get_admin_user),
):
    try:
        normalized_settings = normalize_scholarly_source_settings(
            request.app.state.config.SCHOLARLY_API_SOURCES
        )["sources"]
        current_source_settings = normalized_settings.get(form_data.source_id)
        if current_source_settings is None:
            raise HTTPException(status_code=404, detail="Unknown scholarly source")

        override_payload = (
            form_data.settings_override.model_dump(exclude_none=True)
            if form_data.settings_override is not None
            else {}
        )
        effective_settings = {
            **current_source_settings,
            **override_payload,
        }
        if current_source_settings.get("contact_email"):
            effective_settings["contact_email"] = current_source_settings["contact_email"]
        elif getattr(user, "email", None):
            effective_settings["contact_email"] = str(user.email).strip().lower()

        return await probe_scholarly_source(
            form_data.source_id,
            effective_settings,
            fallback_contact_email=getattr(user, "email", None),
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Failed to test scholarly source")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
