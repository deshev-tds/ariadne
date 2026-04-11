import asyncio
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


os.environ.setdefault("VECTOR_DB", "disabled")


def _install_optional_dependency_stubs() -> None:
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *args, **kwargs: None
    sys.modules.setdefault("boto3", boto3)

    botocore = types.ModuleType("botocore")
    sys.modules.setdefault("botocore", botocore)

    botocore_config = types.ModuleType("botocore.config")

    class Config:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    botocore_config.Config = Config
    sys.modules.setdefault("botocore.config", botocore_config)

    botocore_exceptions = types.ModuleType("botocore.exceptions")

    class ClientError(Exception):
        pass

    botocore_exceptions.ClientError = ClientError
    sys.modules.setdefault("botocore.exceptions", botocore_exceptions)

    google_cloud = types.ModuleType("google.cloud")
    google_cloud.__path__ = []
    sys.modules.setdefault("google.cloud", google_cloud)

    google_cloud_storage = types.ModuleType("google.cloud.storage")
    google_cloud_storage.Client = object
    sys.modules.setdefault("google.cloud.storage", google_cloud_storage)

    google_cloud_exceptions = types.ModuleType("google.cloud.exceptions")

    class GoogleCloudError(Exception):
        pass

    class NotFound(Exception):
        pass

    google_cloud_exceptions.GoogleCloudError = GoogleCloudError
    google_cloud_exceptions.NotFound = NotFound
    sys.modules.setdefault("google.cloud.exceptions", google_cloud_exceptions)

    azure = types.ModuleType("azure")
    azure.__path__ = []
    sys.modules.setdefault("azure", azure)

    azure_identity = types.ModuleType("azure.identity")
    azure_identity.DefaultAzureCredential = object
    azure_identity.get_bearer_token_provider = lambda *args, **kwargs: None
    sys.modules.setdefault("azure.identity", azure_identity)

    azure_storage = types.ModuleType("azure.storage")
    azure_storage.__path__ = []
    sys.modules.setdefault("azure.storage", azure_storage)

    azure_storage_blob = types.ModuleType("azure.storage.blob")
    azure_storage_blob.BlobServiceClient = object
    sys.modules.setdefault("azure.storage.blob", azure_storage_blob)

    azure_core = types.ModuleType("azure.core")
    azure_core.__path__ = []
    sys.modules.setdefault("azure.core", azure_core)

    azure_core_exceptions = types.ModuleType("azure.core.exceptions")

    class ResourceNotFoundError(Exception):
        pass

    azure_core_exceptions.ResourceNotFoundError = ResourceNotFoundError
    sys.modules.setdefault("azure.core.exceptions", azure_core_exceptions)

    retrieval_router = types.ModuleType("open_webui.routers.retrieval")
    retrieval_router.search_web = lambda *args, **kwargs: []
    retrieval_router.execute_strong_source_search = lambda *args, **kwargs: {}
    retrieval_router.process_web_search = lambda *args, **kwargs: {}
    retrieval_router.SearchForm = object
    retrieval_router.process_file = lambda *args, **kwargs: {}
    retrieval_router.ProcessFileForm = object
    sys.modules.setdefault("open_webui.routers.retrieval", retrieval_router)

    retrieval_utils = types.ModuleType("open_webui.retrieval.utils")
    retrieval_utils.get_content_from_url = lambda *args, **kwargs: {}
    retrieval_utils.get_sources_from_items = lambda *args, **kwargs: []
    sys.modules.setdefault("open_webui.retrieval.utils", retrieval_utils)

    images_router = types.ModuleType("open_webui.routers.images")
    images_router.image_generations = lambda *args, **kwargs: {}
    images_router.image_edits = lambda *args, **kwargs: {}
    images_router.get_image_data = lambda *args, **kwargs: (None, None)
    images_router.upload_image = lambda *args, **kwargs: (None, "")
    images_router.CreateImageForm = object
    images_router.EditImageForm = object
    sys.modules.setdefault("open_webui.routers.images", images_router)

    audio_router = types.ModuleType("open_webui.routers.audio")
    audio_router.transcribe = lambda *args, **kwargs: {}
    audio_router.speech = lambda *args, **kwargs: {}
    sys.modules.setdefault("open_webui.routers.audio", audio_router)

    files_router = types.ModuleType("open_webui.routers.files")
    files_router.upload_file_handler = lambda *args, **kwargs: {}
    sys.modules.setdefault("open_webui.routers.files", files_router)

    memories_router = types.ModuleType("open_webui.routers.memories")
    memories_router.query_memory = lambda *args, **kwargs: {}
    memories_router.add_memory = lambda *args, **kwargs: {}
    memories_router.update_memory_by_id = lambda *args, **kwargs: {}
    memories_router.QueryMemoryForm = object
    memories_router.AddMemoryForm = object
    memories_router.MemoryUpdateModel = object
    sys.modules.setdefault("open_webui.routers.memories", memories_router)

    vector_factory = types.ModuleType("open_webui.retrieval.vector.factory")
    vector_factory.VECTOR_DB_CLIENT = None
    sys.modules.setdefault("open_webui.retrieval.vector.factory", vector_factory)

    google_maps_utils = types.ModuleType("open_webui.utils.google_maps")

    class GoogleMapsError(Exception):
        pass

    google_maps_utils.GoogleMapsError = GoogleMapsError
    google_maps_utils.resolve_place_with_google_maps = lambda *args, **kwargs: {}
    sys.modules.setdefault("open_webui.utils.google_maps", google_maps_utils)

    sanitize_utils = types.ModuleType("open_webui.utils.sanitize")
    sanitize_utils.sanitize_code = lambda value, *args, **kwargs: value
    sys.modules.setdefault("open_webui.utils.sanitize", sanitize_utils)

    weather_utils = types.ModuleType("open_webui.utils.weather")

    class WeatherError(Exception):
        pass

    weather_utils.WeatherError = WeatherError
    weather_utils.get_weather_forecast = lambda *args, **kwargs: {}
    sys.modules.setdefault("open_webui.utils.weather", weather_utils)

    retrieval_web_utils = types.ModuleType("open_webui.retrieval.web.utils")
    retrieval_web_utils.validate_url = lambda *args, **kwargs: True
    sys.modules.setdefault("open_webui.retrieval.web.utils", retrieval_web_utils)


_install_optional_dependency_stubs()

import open_webui.retrieval.corpus_runtime as corpus_runtime
import open_webui.retrieval.news_lane as news_lane
import open_webui.tools.builtin as builtin_tools
import open_webui.utils.middleware as middleware
import open_webui.utils.personas as persona_utils
import open_webui.utils.tools as tool_utils


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mini_news_snapshot(root: Path, article_store_root: Path) -> None:
    snapshot = {
        "snapshot_id": "20260410T060000Z",
        "status": "closed",
        "built_at": "2026-04-10T06:00:00Z",
        "dedupe_groups": [
            {
                "group_id": "dedupe:a1",
                "representative_article_id": "a1",
                "duplicate_article_ids": ["a2"],
                "all_article_ids": ["a1", "a2"],
                "dedupe_confidence": 0.92,
                "dedupe_reason": "high_title_similarity",
            },
            {
                "group_id": "dedupe:a3",
                "representative_article_id": "a3",
                "duplicate_article_ids": [],
                "all_article_ids": ["a3"],
                "dedupe_confidence": 1.0,
                "dedupe_reason": "single_article",
            },
        ],
        "representative_articles": [
            {
                "article_id": "a1",
                "dedupe_group_id": "dedupe:a1",
                "duplicate_article_ids": ["a2"],
                "dedupe_confidence": 0.92,
                "dedupe_reason": "high_title_similarity",
                "title": "EU leaders press for new sanctions after weekend strike",
                "url": "https://example.com/a1",
                "source_id": "reuters",
                "published_at": "2026-04-10T05:00:00Z",
                "article_summary_short": "Европейски лидери натискат за нови санкции след удар през уикенда.",
                "entity_keys": ["eu_leaders"],
                "category_ids": ["geopolitics", "europe"],
                "category_scores": {"geopolitics": 0.9, "europe": 0.6},
                "selection_score": 0.88,
                "selection_reason": "High-significance geopolitics item.",
                "editorial_status": "kept",
                "preferred_source_bonus": 0.0,
                "freshness_score": 0.9,
                "activity_score": 0.1,
                "selection_model_id": "gemma",
                "selection_prompt_version": "news-editorial-v1",
            },
            {
                "article_id": "a3",
                "dedupe_group_id": "dedupe:a3",
                "duplicate_article_ids": [],
                "dedupe_confidence": 1.0,
                "dedupe_reason": "single_article",
                "title": "Tiny inference stack lands on Hacker News front page",
                "url": "https://example.com/a3",
                "source_id": "hacker_news",
                "published_at": "2026-04-10T05:30:00Z",
                "article_summary_short": "Малък inference stack изплува на Hacker News и събра прилична инерция.",
                "entity_keys": ["inference_stack"],
                "category_ids": ["tech_ai"],
                "category_scores": {"tech_ai": 0.8, "geopolitics": 0.0},
                "selection_score": 0.74,
                "selection_reason": "Relevant tech item with lighter weight.",
                "editorial_status": "kept",
                "preferred_source_bonus": 0.0,
                "freshness_score": 0.8,
                "activity_score": 0.7,
                "selection_model_id": "gemma",
                "selection_prompt_version": "news-editorial-v1",
            },
        ],
        "kept_article_ids": ["a1", "a3"],
        "dropped_article_ids": [],
        "brief_items": [
            {
                "brief_item_id": "brief:a1",
                "article_id": "a1",
                "headline": "ЕС натиска за нови санкции",
                "title": "EU leaders press for new sanctions after weekend strike",
                "what_happened": "Европейски лидери натискат за нови санкции след удар през уикенда.",
                "why_it_matters": "Това може да ускори нов пакет от европейски мерки и да вдигне напрежението още малко.",
                "paragraph": "Европейски лидери натискат за нови санкции след удар през уикенда. Това може да ускори нов пакет от европейски мерки и да вдигне напрежението още малко.",
                "category_ids": ["geopolitics", "europe"],
                "category_scores": {"geopolitics": 0.9, "europe": 0.6},
                "selection_score": 0.88,
                "selected_for_brief": False,
                "summary_status": "ok",
                "summary_model_id": "gemma",
                "summary_prompt_version": "news-brief-item-v1",
                "source_ref": {
                    "article_id": "a1",
                    "duplicate_article_ids": ["a2"],
                    "source_id": "reuters",
                    "url": "https://example.com/a1",
                    "published_at": "2026-04-10T05:00:00Z",
                    "dedupe_group_id": "dedupe:a1",
                },
            },
            {
                "brief_item_id": "brief:a3",
                "article_id": "a3",
                "headline": "Малък inference stack изплува на HN",
                "title": "Tiny inference stack lands on Hacker News front page",
                "what_happened": "Малък inference stack изплува на Hacker News и събра прилична инерция.",
                "why_it_matters": "Това е сигнал, че локалният AI toolchain пак натиска към по-леки и евтини стекове.",
                "paragraph": "Малък inference stack изплува на Hacker News и събра прилична инерция. Това е сигнал, че локалният AI toolchain пак натиска към по-леки и евтини стекове.",
                "category_ids": ["tech_ai"],
                "category_scores": {"tech_ai": 0.8},
                "selection_score": 0.74,
                "selected_for_brief": False,
                "summary_status": "ok",
                "summary_model_id": "gemma",
                "summary_prompt_version": "news-brief-item-v1",
                "source_ref": {
                    "article_id": "a3",
                    "duplicate_article_ids": [],
                    "source_id": "hacker_news",
                    "url": "https://example.com/a3",
                    "published_at": "2026-04-10T05:30:00Z",
                    "dedupe_group_id": "dedupe:a3",
                },
            },
        ],
    }
    snapshot["stats"] = {
        "article_count": 3,
        "dedupe_group_count": 2,
        "representative_article_count": 2,
        "kept_article_count": 2,
        "dropped_article_count": 0,
        "brief_item_count": 2,
        "summarized_brief_item_count": 2,
        "failed_brief_item_count": 0,
    }
    _write_json(root / "latest_snapshot.json", snapshot)
    _write_json(root / "snapshots" / snapshot["snapshot_id"] / "snapshot.json", snapshot)
    _write_json(root / "snapshots" / snapshot["snapshot_id"] / "brief_items.json", snapshot["brief_items"])
    _write_json(
        root.parent / "news_briefings" / "2026-04-10" / "briefing.json",
        {
            "snapshot_id": "20260410T060000Z",
            "date": "2026-04-10",
            "target_item_count": 7,
            "selected_items": [{**snapshot["brief_items"][0], "selected_for_brief": True}],
            "category_decisions": {},
            "script": snapshot["brief_items"][0]["paragraph"],
            "source_refs": [],
            "audio_path": "/tmp/briefing.wav",
            "tts": {"status": "ok", "engine": "silent_fallback", "path": "/tmp/briefing.wav"},
        },
    )
    _write_json(
        root.parent / "news_briefings" / "latest_briefing.json",
        {
            "snapshot_id": "20260410T060000Z",
            "date": "2026-04-10",
            "target_item_count": 7,
            "selected_items": [{**snapshot["brief_items"][0], "selected_for_brief": True}],
            "category_decisions": {},
            "script": snapshot["brief_items"][0]["paragraph"],
            "source_refs": [],
            "audio_path": "/tmp/briefing.wav",
            "tts": {"status": "ok", "engine": "silent_fallback", "path": "/tmp/briefing.wav"},
        },
    )

    article_payload = {
        "article_id": "a1",
        "source_id": "reuters",
        "url": "https://example.com/a1",
        "title": "EU leaders press for new sanctions after weekend strike",
        "excerpt": "",
        "published_at": "2026-04-10T05:00:00Z",
        "fetched_at": "2026-04-10T05:05:00Z",
        "content_hash": "hash-a1",
        "raw_text_md": "Full article body for a1.",
        "sentence_index": [],
        "discovery_metadata": {},
    }
    analysis_payload = {
        "article_id": "a1",
        "evidence_spans": [
            {
                "span_id": "a1:span:1",
                "article_id": "a1",
                "sentence_ids": ["s1"],
                "char_start": 0,
                "char_end": 40,
                "verbatim_text": "EU leaders said they would coordinate another sanctions package.",
                "attribution": "reported",
                "certainty": "reported",
                "time_scope": "2026-04-10T05:00:00Z",
                "speaker_type": "reporter",
                "claim_kind_hint": "official_position",
                "entity_keys": ["eu_leaders"],
            }
        ],
        "claim_candidates": [],
        "story_hints": {},
        "domain_scores": {"geopolitics": 0.9, "europe": 0.6},
        "novelty_score": 0.9,
        "activity_score": 0.1,
        "needs_context": True,
    }
    _write_json(article_store_root / "articles" / "a1" / "article.json", article_payload)
    _write_json(article_store_root / "articles" / "a1" / "analysis.json", analysis_payload)


@pytest.fixture
def news_fixture(tmp_path):
    article_store_root = tmp_path / "news_articles"
    corpus_root = tmp_path / "news_corpus"
    briefings_root = tmp_path / "news_briefings"
    _mini_news_snapshot(corpus_root, article_store_root)
    config = SimpleNamespace(
        ENABLE_LOCAL_CORPUS_TOOLS=False,
        LOCAL_CORPUS_ROOT="",
        OFFSEC_CORPUS_ROOT="",
        NEWS_ENABLED=True,
        NEWS_ARTICLE_STORE_ROOT=str(article_store_root),
        NEWS_CORPUS_ROOT=str(corpus_root),
        NEWS_BRIEFINGS_ROOT=str(briefings_root),
        NEWS_SOURCE_REGISTRY=news_lane.default_news_source_registry(),
        NEWS_CATEGORY_CONFIG=news_lane.default_news_category_config(),
        NEWS_BRIEF_TARGET_ITEM_COUNT=7,
        NEWS_TTS_VOICE_ID="bg",
        TASK_MODEL="",
        TASK_MODEL_EXTERNAL=False,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))
    return {
        "config": config,
        "request": request,
        "article_store_root": article_store_root,
        "corpus_root": corpus_root,
        "briefings_root": briefings_root,
    }


def test_news_category_semantic_hash_ignores_ui_only_fields():
    categories = news_lane.default_news_category_config()
    mutated = json.loads(json.dumps(categories))
    mutated[0]["label"] = "Geo"
    mutated[0]["help_text"] = "Changed"
    mutated.reverse()
    mutated[0]["display_order"] = 999

    assert news_lane.news_category_config_semantic_hash(
        categories, source_registry=news_lane.default_news_source_registry()
    ) == news_lane.news_category_config_semantic_hash(
        mutated, source_registry=news_lane.default_news_source_registry()
    )


def test_news_source_registry_semantic_hash_ignores_label_only_changes():
    registry = news_lane.default_news_source_registry()
    mutated = json.loads(json.dumps(registry))
    mutated[0]["label"] = "Reuters Wire"

    assert news_lane.news_source_registry_semantic_hash(
        registry
    ) == news_lane.news_source_registry_semantic_hash(mutated)


def test_compute_story_candidate_score_caps_preferred_source_bonus():
    story = {
        "brief_item_id": "brief:a1",
        "summary_status": "ok",
        "selection_score": 0.9,
        "category_scores": {"bulgaria": 0.9},
        "source_ref": {"source_id": "kapital"},
    }
    category = {
        "category_id": "bulgaria",
        "preferred_source_ids": ["kapital", "dnevnik"],
    }

    score = news_lane.compute_story_candidate_score(story, category=category)

    assert score["preferred_bonus_applied"] is True
    assert score["preferred_bonus"] == pytest.approx(news_lane.NEWS_PREFERRED_SOURCE_BONUS_CAP)
    assert score["final_score"] <= 1.0


def test_compute_story_candidate_score_blocks_unsummarized_story():
    score = news_lane.compute_story_candidate_score(
        {
            "brief_item_id": "brief:a1",
            "summary_status": "pending_retry",
        },
        category={"category_id": "geopolitics", "preferred_source_ids": []},
    )

    assert score["selection_eligible"] is False
    assert score["selection_block_reason"] == "summary_status:pending_retry"


def test_compute_story_candidate_score_uses_selection_score():
    lower = news_lane.compute_story_candidate_score(
        {
            "brief_item_id": "brief:a1",
            "summary_status": "ok",
            "selection_score": 0.4,
            "category_scores": {"geopolitics": 0.9},
            "source_ref": {"source_id": "reuters"},
        },
        category={"category_id": "geopolitics", "preferred_source_ids": []},
    )
    higher = news_lane.compute_story_candidate_score(
        {
            "brief_item_id": "brief:a2",
            "summary_status": "ok",
            "selection_score": 0.8,
            "category_scores": {"geopolitics": 0.9},
            "source_ref": {"source_id": "reuters"},
        },
        category={"category_id": "geopolitics", "preferred_source_ids": []},
    )

    assert higher["selection_eligible"] is True
    assert higher["thread_penalty"] == 0.0
    assert higher["final_score"] > lower["final_score"]


def test_news_model_timeouts_default_to_batch_friendly_values():
    config = SimpleNamespace()

    assert news_lane._news_model_timeout_seconds("article", config_or_path=config) == 300
    assert news_lane._news_model_timeout_seconds("brief", config_or_path=config) == 300


def test_news_model_timeouts_are_sanitized_and_clamped():
    config = SimpleNamespace(
        NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS="0",
        NEWS_BRIEF_MODEL_TIMEOUT_SECONDS="garbage",
    )

    assert news_lane._news_model_timeout_seconds("article", config_or_path=config) == 5
    assert news_lane._news_model_timeout_seconds("brief", config_or_path=config) == 300


def test_morning_news_persona_form_uses_news_defaults(news_fixture):
    form = persona_utils.build_morning_news_persona_form(news_fixture["config"])

    assert form.name == persona_utils.MORNING_NEWS_PERSONA_NAME
    assert form.bound_model_id is None
    assert form.voice_id == "bg"
    assert form.capabilities["preferred_working_mode"] == "news"
    assert form.default_feature_ids == ["voice"]


def test_resolve_corpus_runtime_enables_news_mode(news_fixture):
    runtime = corpus_runtime.resolve_corpus_runtime(
        news_fixture["config"],
        {"working_mode": "news", "local_corpus_mode": "off"},
    )

    assert runtime.working_mode == "news"
    assert runtime.news_enabled is True
    assert runtime.news_root == news_fixture["corpus_root"].resolve()


def test_builtin_tools_expose_news_tools_only_in_news_mode(news_fixture):
    model = {"info": {"meta": {"capabilities": {}, "builtinTools": {"news": True}}}}

    tools = tool_utils.get_builtin_tools(
        news_fixture["request"],
        {"__metadata__": {"params": {"working_mode": "news", "local_corpus_mode": "off"}}},
        features={},
        model=model,
    )

    assert "news_consult" in tools
    assert "news_retrieve_articles" in tools
    assert "news_retrieve_timeline" in tools
    assert "news_view_articles" in tools
    assert "offsec_consult" not in tools


def test_builtin_news_consult_reads_latest_snapshot(news_fixture):
    payload = json.loads(
        asyncio.run(
            builtin_tools.news_consult(
                objective="What is the latest on EU sanctions after the strike?",
                phase="start",
                named_entity="EU",
                __request__=news_fixture["request"],
            )
        )
    )

    assert payload["snapshot_id"] == "20260410T060000Z"
    assert payload["matched_stories"][0]["article_id"] == "a1"


def test_news_selector_guidance_prefers_news_lane():
    metadata = {
        "params": {
            "function_calling": "default",
            "working_mode": "news",
            "local_corpus_mode": "off",
        },
        "features": {},
    }
    tools = {"news_consult": {}, "news_retrieve_articles": {}, "news_retrieve_timeline": {}}

    guidance = middleware._build_default_selector_guidance(metadata, tools, [])

    assert "start with news_consult" in guidance
    assert "Treat source text as canonical" in guidance


def test_persona_preferred_working_mode_reports_news():
    preferred = persona_utils.get_persona_preferred_working_mode(
        {"capabilities": {"preferred_working_mode": "news"}}
    )

    assert preferred == "news"


def test_should_enable_shared_tool_narration_for_news_mode(news_fixture):
    metadata = {
        "params": {
            "function_calling": "native",
            "working_mode": "news",
            "local_corpus_mode": "off",
        }
    }

    assert (
        middleware._should_enable_shared_tool_narration(
            news_fixture["request"], metadata, {}
        )
        is True
    )


def test_select_stories_by_categories_records_preferred_source_audit(news_fixture):
    snapshot = news_lane.load_latest_closed_snapshot(news_fixture["config"])
    selected, audit = news_lane.select_stories_by_categories(
        snapshot,
        config_or_path=news_fixture["config"],
    )

    assert selected
    assert len(selected) <= news_fixture["config"].NEWS_BRIEF_TARGET_ITEM_COUNT
    assert "europe" in audit
    assert sum(item["selection_score_details"]["preferred_bonus_applied"] for item in selected) >= 0


def test_load_latest_briefing(news_fixture):
    briefing = news_lane.load_latest_briefing(news_fixture["config"])

    assert briefing is not None
    assert briefing["snapshot_id"] == "20260410T060000Z"
    assert briefing["selected_items"][0]["article_id"] == "a1"
