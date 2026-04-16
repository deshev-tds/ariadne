import logging
import time
from uuid import uuid4
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import ChatForm, Chats
from open_webui.models.simon_lex_index import enqueue_message
from open_webui.models.users import Users
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
    normalize_and_persist_news_storage_roots,
    render_briefing_chat_markdown,
    play_latest_briefing,
    prefetch_related_once,
    analyze_articles,
    build_snapshot,
    validate_news_category_config_payload,
    validate_news_source_registry_payload,
)
from open_webui.utils.auth import get_admin_user
from open_webui.utils.personas import (
    MORNING_NEWS_PERSONA_NAME,
    build_persona_defaults_snapshot,
    ensure_morning_news_persona_for_user,
)

log = logging.getLogger(__name__)

router = APIRouter()
DEFAULT_NEWS_WAKE_TIME = "05:08"


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


def normalize_news_wake_time(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_NEWS_WAKE_TIME
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError("NEWS_WAKE_TIME must use HH:MM format.")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("NEWS_WAKE_TIME must use numeric HH:MM format.") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("NEWS_WAKE_TIME must be between 00:00 and 23:59.")
    return f"{hour:02d}:{minute:02d}"


def coerce_news_wake_time(value: str | None) -> str:
    try:
        return normalize_news_wake_time(value)
    except ValueError:
        return DEFAULT_NEWS_WAKE_TIME


def _refresh_news_scheduler(request: Request) -> None:
    refresh = getattr(request.app.state, "refresh_news_scheduler", None)
    if callable(refresh):
        refresh()


def run_news_snapshot_pipeline(config_or_path: Any) -> dict[str, Any]:
    fetch_result = discover_and_fetch_news(config_or_path=config_or_path)
    prefetch_result = prefetch_related_once(
        config_or_path=config_or_path,
        article_ids=fetch_result.get("fetched_article_ids", []),
    )
    analysis_result = analyze_articles(
        config_or_path=config_or_path,
        article_ids=fetch_result.get("fetched_article_ids", []),
    )
    snapshot = build_snapshot(
        config_or_path=config_or_path,
        article_ids=fetch_result.get("fetched_article_ids", []),
        prefetched_article_ids=prefetch_result.get("prefetched_article_ids", []),
    )
    return {
        "fetch": fetch_result,
        "prefetch": prefetch_result,
        "analysis": analysis_result,
        "snapshot": snapshot,
    }


def run_news_daily_briefing_pipeline(config_or_path: Any) -> dict[str, Any]:
    snapshot = load_latest_closed_snapshot(config_or_path)
    briefing = build_briefing(
        config_or_path=config_or_path,
        snapshot=snapshot,
    )
    briefing["materialized_chats"] = materialize_morning_news_chats(
        config_or_path=config_or_path,
        briefing=briefing,
    )
    return {"briefing": briefing}


def run_news_morning_pipeline(config_or_path: Any) -> dict[str, Any]:
    snapshot_result = run_news_snapshot_pipeline(config_or_path)
    briefing = build_briefing(
        config_or_path=config_or_path,
        snapshot=snapshot_result["snapshot"],
    )
    briefing["materialized_chats"] = materialize_morning_news_chats(
        config_or_path=config_or_path,
        briefing=briefing,
    )
    return {
        **snapshot_result,
        "briefing": briefing,
    }


def _materialized_news_chat_title(briefing: dict[str, Any]) -> str:
    return f"{MORNING_NEWS_PERSONA_NAME} · {briefing.get('date') or 'Undated'}"


def _build_materialized_news_chat_message(
    *,
    message_id: str,
    content: str,
    timestamp: int,
    model_id: Optional[str],
) -> dict[str, Any]:
    message = {
        "id": message_id,
        "role": "assistant",
        "content": content,
        "timestamp": timestamp,
        "done": True,
        "parentId": None,
        "childrenIds": [],
    }
    if model_id:
        message["model"] = model_id
    return message


def _build_materialized_news_chat_payload(
    *,
    title: str,
    content: str,
    message_id: str,
    timestamp: int,
    model_id: Optional[str],
) -> dict[str, Any]:
    message = _build_materialized_news_chat_message(
        message_id=message_id,
        content=content,
        timestamp=timestamp,
        model_id=model_id,
    )
    return {
        "title": title,
        "timestamp": timestamp,
        "models": [model_id] if model_id else [],
        "history": {
            "currentId": message_id,
            "messages": {message_id: message},
        },
    }


def _build_materialized_news_chat_meta(
    *,
    persona,
    briefing: dict[str, Any],
    root_message_id: str,
    timestamp: int,
) -> dict[str, Any]:
    return {
        "persona_defaults_snapshot": build_persona_defaults_snapshot(persona),
        "persona_chat_overrides": {},
        "news_materialization": {
            "kind": "daily_briefing_chat",
            "date": briefing.get("date"),
            "snapshot_id": briefing.get("snapshot_id"),
            "root_message_id": root_message_id,
            "audio_path": briefing.get("audio_path") or "",
            "target_item_count": int(briefing.get("target_item_count") or 0),
            "materialized_at": timestamp,
        },
    }


def _find_existing_materialized_news_chat(user_id: str, briefing_date: str) -> Optional[Any]:
    chats = Chats.get_chat_list_by_user_id(
        user_id,
        include_archived=True,
        limit=None,
    )
    for chat in chats:
        materialization = (chat.meta or {}).get("news_materialization") or {}
        if (
            materialization.get("kind") == "daily_briefing_chat"
            and str(materialization.get("date") or "") == str(briefing_date or "")
        ):
            return chat
    return None


def _chat_has_materialized_news_activity(existing_chat: Any, root_message_id: str) -> bool:
    history = dict((existing_chat.chat or {}).get("history") or {})
    messages = dict(history.get("messages") or {})
    if len(messages) != 1:
        return True
    current_id = history.get("currentId")
    if current_id and root_message_id and current_id != root_message_id:
        return True
    root_message = messages.get(root_message_id) or messages.get(current_id) or {}
    return bool(root_message.get("childrenIds"))


def _sync_materialized_news_root_message(
    *,
    chat_id: str,
    user_id: str,
    message_id: str,
    message: dict[str, Any],
) -> None:
    try:
        ChatMessages.upsert_message(
            message_id=message_id,
            chat_id=chat_id,
            user_id=user_id,
            data=message,
        )
        enqueue_message(chat_id, message_id, priority=1)
    except Exception as exc:
        log.warning("Failed to sync materialized news root message for chat %s: %s", chat_id, exc)


def materialize_morning_news_chats(
    *,
    config_or_path: Any,
    briefing: dict[str, Any],
) -> list[dict[str, Any]]:
    briefing_date = str(briefing.get("date") or "").strip()
    if not briefing_date:
        return []

    content = str(briefing.get("chat_markdown") or "").strip() or render_briefing_chat_markdown(
        briefing
    )
    title = _materialized_news_chat_title(briefing)
    admins = Users.get_users(filter={"roles": ["admin"]}).get("users", [])
    materialized: list[dict[str, Any]] = []

    for admin in admins:
        persona = ensure_morning_news_persona_for_user(admin.id, config_or_path)
        if persona is None:
            continue

        existing_chat = _find_existing_materialized_news_chat(admin.id, briefing_date)
        existing_materialization = (
            (existing_chat.meta or {}).get("news_materialization") if existing_chat else {}
        ) or {}
        root_message_id = str(
            existing_materialization.get("root_message_id")
            or ((existing_chat.chat or {}) if existing_chat else {}).get("history", {}).get("currentId")
            or uuid4()
        )
        timestamp = int(time.time())
        payload = _build_materialized_news_chat_payload(
            title=title,
            content=content,
            message_id=root_message_id,
            timestamp=timestamp,
            model_id=getattr(persona, "bound_model_id", None),
        )
        meta = _build_materialized_news_chat_meta(
            persona=persona,
            briefing=briefing,
            root_message_id=root_message_id,
            timestamp=timestamp,
        )

        if existing_chat is None:
            inserted = Chats.insert_new_chat(
                admin.id,
                ChatForm(
                    chat=payload,
                    meta=meta,
                    persona_id=persona.id,
                ),
            )
            if inserted is not None:
                materialized.append(
                    {
                        "user_id": admin.id,
                        "chat_id": inserted.id,
                        "title": inserted.title,
                        "status": "created",
                    }
                )
            continue

        if _chat_has_materialized_news_activity(existing_chat, root_message_id):
            materialized.append(
                {
                    "user_id": admin.id,
                    "chat_id": existing_chat.id,
                    "title": existing_chat.title,
                    "status": "skipped_due_to_chat_activity",
                }
            )
            continue

        updated = Chats.update_chat_by_id(
            existing_chat.id,
            payload,
            meta=meta,
            persona_id=persona.id,
        )
        if updated is not None:
            _sync_materialized_news_root_message(
                chat_id=updated.id,
                user_id=admin.id,
                message_id=root_message_id,
                message=payload["history"]["messages"][root_message_id],
            )
            materialized.append(
                {
                    "user_id": admin.id,
                    "chat_id": updated.id,
                    "title": updated.title,
                    "status": "updated",
                }
            )

    return materialized


def _news_config_payload(request: Request) -> dict[str, Any]:
    config = request.app.state.config
    normalize_and_persist_news_storage_roots(config)
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
        "NEWS_WAKE_TIME": coerce_news_wake_time(str(config.NEWS_WAKE_TIME or "")),
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
    try:
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
                1, min(24, int(form_data.NEWS_BRIEF_TARGET_ITEM_COUNT))
            )
        if form_data.NEWS_TTS_VOICE_ID is not None:
            config.NEWS_TTS_VOICE_ID = form_data.NEWS_TTS_VOICE_ID
        if form_data.NEWS_WAKE_TIME is not None:
            config.NEWS_WAKE_TIME = normalize_news_wake_time(form_data.NEWS_WAKE_TIME)
        if form_data.NEWS_PLAYBACK_DEVICE is not None:
            config.NEWS_PLAYBACK_DEVICE = form_data.NEWS_PLAYBACK_DEVICE
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    normalize_and_persist_news_storage_roots(config)
    _refresh_news_scheduler(request)
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


@router.post("/worker/run-morning")
async def run_news_morning(request: Request, user=Depends(get_admin_user)):
    try:
        result = run_news_morning_pipeline(request.app.state.config)
        return {"status": True, **result}
    except Exception as exc:
        log.exception("News morning worker failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(exc),
        )


@router.post("/worker/run-hourly")
async def run_news_hourly(request: Request, user=Depends(get_admin_user)):
    try:
        result = run_news_morning_pipeline(request.app.state.config)
        return {"status": True, **result}
    except Exception as exc:
        log.exception("News morning worker alias failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(exc),
        )


@router.post("/worker/run-daily")
async def run_news_daily(request: Request, user=Depends(get_admin_user)):
    try:
        return {"status": True, **run_news_daily_briefing_pipeline(request.app.state.config)}
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
