import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.google_maps import probe_google_maps_integration
from open_webui.utils.auth import get_admin_user


log = logging.getLogger(__name__)
router = APIRouter()


class MapsConfig(BaseModel):
    ENABLE_GOOGLE_MAPS: Optional[bool] = None
    GOOGLE_MAPS_API_KEY: Optional[str] = None
    GOOGLE_MAPS_BASE_URL: Optional[str] = None
    GOOGLE_MAPS_TIMEOUT_SECONDS: Optional[int] = None
    GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE: Optional[str] = None
    GOOGLE_MAPS_DEFAULT_REGION_CODE: Optional[str] = None
    GOOGLE_MAPS_MAX_CANDIDATES: Optional[int] = None


class MapsConfigForm(BaseModel):
    maps: Optional[MapsConfig] = None


class MapsTestForm(BaseModel):
    place_name: str
    location_context: Optional[str] = None
    query_hint: Optional[str] = None
    language_code: Optional[str] = None
    region_code: Optional[str] = None
    max_candidates: Optional[int] = None


def _maps_config_payload(request: Request) -> dict:
    return {
        "ENABLE_GOOGLE_MAPS": request.app.state.config.ENABLE_GOOGLE_MAPS,
        "GOOGLE_MAPS_API_KEY": request.app.state.config.GOOGLE_MAPS_API_KEY,
        "GOOGLE_MAPS_BASE_URL": request.app.state.config.GOOGLE_MAPS_BASE_URL,
        "GOOGLE_MAPS_TIMEOUT_SECONDS": request.app.state.config.GOOGLE_MAPS_TIMEOUT_SECONDS,
        "GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE": request.app.state.config.GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE,
        "GOOGLE_MAPS_DEFAULT_REGION_CODE": request.app.state.config.GOOGLE_MAPS_DEFAULT_REGION_CODE,
        "GOOGLE_MAPS_MAX_CANDIDATES": request.app.state.config.GOOGLE_MAPS_MAX_CANDIDATES,
    }


@router.get("/config")
async def get_maps_config(request: Request, user=Depends(get_admin_user)):
    return {"status": True, "maps": _maps_config_payload(request)}


@router.post("/config/update")
async def update_maps_config(
    request: Request,
    form_data: MapsConfigForm,
    user=Depends(get_admin_user),
):
    try:
        maps = form_data.maps or MapsConfig()

        if maps.ENABLE_GOOGLE_MAPS is not None:
            request.app.state.config.ENABLE_GOOGLE_MAPS = maps.ENABLE_GOOGLE_MAPS
        if maps.GOOGLE_MAPS_API_KEY is not None:
            request.app.state.config.GOOGLE_MAPS_API_KEY = maps.GOOGLE_MAPS_API_KEY
        if maps.GOOGLE_MAPS_BASE_URL is not None:
            request.app.state.config.GOOGLE_MAPS_BASE_URL = maps.GOOGLE_MAPS_BASE_URL
        if maps.GOOGLE_MAPS_TIMEOUT_SECONDS is not None:
            request.app.state.config.GOOGLE_MAPS_TIMEOUT_SECONDS = (
                maps.GOOGLE_MAPS_TIMEOUT_SECONDS
            )
        if maps.GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE is not None:
            request.app.state.config.GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE = (
                maps.GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE
            )
        if maps.GOOGLE_MAPS_DEFAULT_REGION_CODE is not None:
            request.app.state.config.GOOGLE_MAPS_DEFAULT_REGION_CODE = (
                maps.GOOGLE_MAPS_DEFAULT_REGION_CODE
            )
        if maps.GOOGLE_MAPS_MAX_CANDIDATES is not None:
            request.app.state.config.GOOGLE_MAPS_MAX_CANDIDATES = (
                maps.GOOGLE_MAPS_MAX_CANDIDATES
            )

        return {"status": True, "maps": _maps_config_payload(request)}
    except Exception as e:
        log.exception("Failed to update Google Maps config")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/test")
async def test_maps_config(
    request: Request,
    form_data: MapsTestForm,
    user=Depends(get_admin_user),
):
    try:
        return probe_google_maps_integration(
            config=request.app.state.config,
            request=request,
            place_name=form_data.place_name,
            location_context=form_data.location_context,
            query_hint=form_data.query_hint,
            language_code=form_data.language_code,
            region_code=form_data.region_code,
            max_candidates=form_data.max_candidates,
        )
    except Exception as e:
        log.exception("Failed to test Google Maps integration")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
