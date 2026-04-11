import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.retrieval.news_lane import (
    build_briefing,
    default_news_category_config,
    default_news_source_registry,
    discover_and_fetch_news,
    load_latest_briefing,
    load_latest_closed_snapshot,
    load_news_thread_view,
    load_news_category_config,
    load_news_source_registry,
    news_category_config_semantic_hash,
    news_source_registry_semantic_hash,
    normalize_news_category_config_payload,
    normalize_news_source_registry_payload,
    play_latest_briefing,
    prefetch_related_once,
    analyze_articles,
    build_snapshot,
    validate_news_category_config_payload,
    validate_news_source_registry_payload,
)
from open_webui.utils.auth import get_admin_user

log = logging.getLogger(__name__)

router = APIRouter()


class NewsConfigForm(BaseModel):
    NEWS_ENABLED: Optional[bool] = None
    NEWS_ARTICLE_STORE_ROOT: Optional[str] = None
    NEWS_CORPUS_ROOT: Optional[str] = None
    NEWS_BRIEFINGS_ROOT: Optional[str] = None
    NEWS_ARTICLE_MODEL_ENDPOINT: Optional[str] = None
    NEWS_ARTICLE_MODEL: Optional[str] = None
    NEWS_BRIEF_MODEL: Optional[str] = None
    NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS: Optional[int] = None
    NEWS_BRIEF_MODEL_TIMEOUT_SECONDS: Optional[int] = None
    NEWS_BRIEF_TARGET_ITEM_COUNT: Optional[int] = None
    NEWS_TTS_VOICE_ID: Optional[str] = None
    NEWS_WAKE_TIME: Optional[str] = None
    NEWS_PLAYBACK_DEVICE: Optional[str] = None


class RegistryUpdateForm(BaseModel):
    registry: list[dict[str, Any]]


class CategoriesUpdateForm(BaseModel):
    categories: list[dict[str, Any]]


def _news_config_payload(request: Request) -> dict[str, Any]:
    config = request.app.state.config
    registry = load_news_source_registry(config)
    categories = load_news_category_config(config)
    return {
        "NEWS_ENABLED": bool(config.NEWS_ENABLED),
        "NEWS_ARTICLE_STORE_ROOT": str(config.NEWS_ARTICLE_STORE_ROOT),
        "NEWS_CORPUS_ROOT": str(config.NEWS_CORPUS_ROOT),
        "NEWS_BRIEFINGS_ROOT": str(config.NEWS_BRIEFINGS_ROOT),
        "NEWS_ARTICLE_MODEL_ENDPOINT": str(config.NEWS_ARTICLE_MODEL_ENDPOINT or ""),
        "NEWS_ARTICLE_MODEL": str(config.NEWS_ARTICLE_MODEL or ""),
        "NEWS_BRIEF_MODEL": str(config.NEWS_BRIEF_MODEL or ""),
        "NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS": int(config.NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS),
        "NEWS_BRIEF_MODEL_TIMEOUT_SECONDS": int(config.NEWS_BRIEF_MODEL_TIMEOUT_SECONDS),
        "NEWS_BRIEF_TARGET_ITEM_COUNT": int(config.NEWS_BRIEF_TARGET_ITEM_COUNT),
        "NEWS_TTS_VOICE_ID": str(config.NEWS_TTS_VOICE_ID or ""),
        "NEWS_WAKE_TIME": str(config.NEWS_WAKE_TIME or ""),
        "NEWS_PLAYBACK_DEVICE": str(config.NEWS_PLAYBACK_DEVICE or ""),
        "source_registry_semantic_hash": news_source_registry_semantic_hash(registry),
        "category_config_semantic_hash": news_category_config_semantic_hash(
            categories, source_registry=registry
        ),
    }


@router.get("/config")
async def get_news_config(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        "config": _news_config_payload(request),
    }


@router.post("/config")
async def update_news_config(
    request: Request,
    form_data: NewsConfigForm,
    user=Depends(get_admin_user),
):
    config = request.app.state.config
    if form_data.NEWS_ENABLED is not None:
        config.NEWS_ENABLED = bool(form_data.NEWS_ENABLED)
    if form_data.NEWS_ARTICLE_STORE_ROOT is not None:
        config.NEWS_ARTICLE_STORE_ROOT = form_data.NEWS_ARTICLE_STORE_ROOT
    if form_data.NEWS_CORPUS_ROOT is not None:
        config.NEWS_CORPUS_ROOT = form_data.NEWS_CORPUS_ROOT
    if form_data.NEWS_BRIEFINGS_ROOT is not None:
        config.NEWS_BRIEFINGS_ROOT = form_data.NEWS_BRIEFINGS_ROOT
    if form_data.NEWS_ARTICLE_MODEL_ENDPOINT is not None:
        config.NEWS_ARTICLE_MODEL_ENDPOINT = form_data.NEWS_ARTICLE_MODEL_ENDPOINT
    if form_data.NEWS_ARTICLE_MODEL is not None:
        config.NEWS_ARTICLE_MODEL = form_data.NEWS_ARTICLE_MODEL
    if form_data.NEWS_BRIEF_MODEL is not None:
        config.NEWS_BRIEF_MODEL = form_data.NEWS_BRIEF_MODEL
    if form_data.NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS is not None:
        config.NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS = max(
            5, int(form_data.NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS)
        )
    if form_data.NEWS_BRIEF_MODEL_TIMEOUT_SECONDS is not None:
        config.NEWS_BRIEF_MODEL_TIMEOUT_SECONDS = max(
            5, int(form_data.NEWS_BRIEF_MODEL_TIMEOUT_SECONDS)
        )
    if form_data.NEWS_BRIEF_TARGET_ITEM_COUNT is not None:
        config.NEWS_BRIEF_TARGET_ITEM_COUNT = max(
            1, min(20, int(form_data.NEWS_BRIEF_TARGET_ITEM_COUNT))
        )
    if form_data.NEWS_TTS_VOICE_ID is not None:
        config.NEWS_TTS_VOICE_ID = form_data.NEWS_TTS_VOICE_ID
    if form_data.NEWS_WAKE_TIME is not None:
        config.NEWS_WAKE_TIME = form_data.NEWS_WAKE_TIME
    if form_data.NEWS_PLAYBACK_DEVICE is not None:
        config.NEWS_PLAYBACK_DEVICE = form_data.NEWS_PLAYBACK_DEVICE
    return {"status": True, "config": _news_config_payload(request)}


@router.get("/source-registry")
async def get_news_source_registry(request: Request, user=Depends(get_admin_user)):
    registry = load_news_source_registry(request.app.state.config)
    validation = validate_news_source_registry_payload(registry)
    return {
        "status": True,
        "registry": registry,
        "validation": validation,
        "semantic_hash": news_source_registry_semantic_hash(registry),
    }


@router.post("/source-registry")
async def update_news_source_registry(
    request: Request,
    form_data: RegistryUpdateForm,
    user=Depends(get_admin_user),
):
    try:
        registry = normalize_news_source_registry_payload(form_data.registry)
        validation = validate_news_source_registry_payload(registry)
        request.app.state.config.NEWS_SOURCE_REGISTRY = registry
        return {
            "status": True,
            "registry": registry,
            "validation": validation,
            "semantic_hash": news_source_registry_semantic_hash(registry),
        }
    except Exception as exc:
        log.exception("Failed to update news source registry")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(exc),
        )


@router.get("/categories")
async def get_news_categories(request: Request, user=Depends(get_admin_user)):
    registry = load_news_source_registry(request.app.state.config)
    categories = load_news_category_config(request.app.state.config)
    validation = validate_news_category_config_payload(
        categories,
        source_registry=registry,
    )
    return {
        "status": True,
        "categories": categories,
        "validation": validation,
        "semantic_hash": news_category_config_semantic_hash(
            categories, source_registry=registry
        ),
    }


@router.post("/categories")
async def update_news_categories(
    request: Request,
    form_data: CategoriesUpdateForm,
    user=Depends(get_admin_user),
):
    try:
        registry = load_news_source_registry(request.app.state.config)
        categories = normalize_news_category_config_payload(
            form_data.categories,
            source_registry=registry,
        )
        validation = validate_news_category_config_payload(
            categories,
            source_registry=registry,
        )
        request.app.state.config.NEWS_CATEGORY_CONFIG = categories
        return {
            "status": True,
            "categories": categories,
            "validation": validation,
            "semantic_hash": news_category_config_semantic_hash(
                categories, source_registry=registry
            ),
        }
    except Exception as exc:
        log.exception("Failed to update news categories")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(exc),
        )


@router.get("/latest-snapshot")
async def get_latest_news_snapshot(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        "snapshot": load_latest_closed_snapshot(request.app.state.config),
    }


@router.get("/latest-briefing")
async def get_latest_news_briefing(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        "briefing": load_latest_briefing(request.app.state.config),
    }


@router.get("/threads/{thread_id}")
async def get_news_thread(thread_id: str, request: Request, user=Depends(get_admin_user)):
    payload = load_news_thread_view(thread_id, config_or_path=request.app.state.config)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown news thread: {thread_id}",
        )
    return {"status": True, **payload}


@router.post("/worker/run-hourly")
async def run_news_hourly(request: Request, user=Depends(get_admin_user)):
    try:
        fetch_result = discover_and_fetch_news(config_or_path=request.app.state.config)
        prefetch_result = prefetch_related_once(
            config_or_path=request.app.state.config,
            article_ids=fetch_result.get("fetched_article_ids", []),
        )
        analysis_result = analyze_articles(
            config_or_path=request.app.state.config,
            article_ids=fetch_result.get("fetched_article_ids", []),
        )
        snapshot = build_snapshot(
            config_or_path=request.app.state.config,
            article_ids=fetch_result.get("fetched_article_ids", []),
            prefetched_article_ids=prefetch_result.get("prefetched_article_ids", []),
        )
        return {
            "status": True,
            "fetch": fetch_result,
            "prefetch": prefetch_result,
            "analysis": analysis_result,
            "snapshot": snapshot,
        }
    except Exception as exc:
        log.exception("News hourly worker failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(exc),
        )


@router.post("/worker/run-daily")
async def run_news_daily(request: Request, user=Depends(get_admin_user)):
    try:
        snapshot = load_latest_closed_snapshot(request.app.state.config)
        briefing = build_briefing(
            config_or_path=request.app.state.config,
            snapshot=snapshot,
        )
        return {"status": True, "briefing": briefing}
    except Exception as exc:
        log.exception("News daily worker failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(exc),
        )


@router.post("/worker/play-latest")
async def play_news_latest(request: Request, user=Depends(get_admin_user)):
    try:
        result = play_latest_briefing(config_or_path=request.app.state.config)
        return {"status": True, "result": result}
    except Exception as exc:
        log.exception("News playback failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(exc),
        )
