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

    google = types.ModuleType("google")
    google.__path__ = []
    sys.modules.setdefault("google", google)

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
import open_webui.routers.news as news_router
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


def test_canonicalize_category_scores_filters_non_admin_keys():
    categories = news_lane.default_news_category_config()

    canonical = news_lane._canonicalize_category_scores(
        {"geopolitics": 0.9, "middle_east": 0.95, "politics": 0.8},
        categories=categories,
        base_scores={"economy": 0.4},
    )

    assert canonical == {"economy": 0.4, "geopolitics": 0.9}


def test_canonicalize_category_scores_ignores_malformed_model_payload():
    categories = news_lane.default_news_category_config()

    assert news_lane._canonicalize_category_scores(
        "middle_east",
        categories=categories,
        base_scores={"geopolitics": 0.6},
    ) == {"geopolitics": 0.6}


def test_build_brief_items_canonicalizes_model_category_scores(monkeypatch):
    categories = news_lane.default_news_category_config()
    article = {
        "article_id": "a1",
        "source_id": "bbc_news",
        "url": "https://example.com/a1",
        "title": "Example article",
        "published_at": "2026-04-10T05:00:00Z",
        "raw_text_md": "Body",
    }
    analysis = {
        "article_summary_short": "Short summary",
        "entity_keys": ["entity_a"],
        "key_facts": ["fact_a"],
    }
    representative = {
        "article_id": "a1",
        "dedupe_group_id": "dedupe:a1",
        "duplicate_article_ids": [],
        "category_scores": {"geopolitics": 0.8, "europe": 0.5},
        "selection_score": 0.9,
        "editorial_status": "kept",
        "entity_keys": ["entity_a"],
    }
    config = SimpleNamespace(
        NEWS_CATEGORY_CONFIG=categories,
        NEWS_BRIEF_MODEL="gemma",
        NEWS_ARTICLE_MODEL="gemma",
        NEWS_ARTICLE_MODEL_ENDPOINT="http://example.com",
        NEWS_BRIEF_MODEL_TIMEOUT_SECONDS=300,
    )

    monkeypatch.setattr(
        news_lane,
        "_summarize_brief_item_with_model",
        lambda *args, **kwargs: {
            "headline": "Заглавие",
            "what_happened": "Стана нещо важно.",
            "why_it_matters": "Има значение.",
            "paragraph": "Това е един по-пълен параграф с контекст, а не телеграфно изречение.",
            "category_scores": {"middle_east": 0.95, "geopolitics": 0.91},
        },
    )

    items = news_lane._build_brief_items(
        [representative],
        {"a1": article},
        {"a1": analysis},
        config_or_path=config,
    )

    assert len(items) == 1
    assert items[0]["category_scores"] == {"geopolitics": 0.91, "europe": 0.5}
    assert items[0]["category_ids"] == ["geopolitics", "europe"]


def test_brief_item_prompt_targets_longer_paragraphs(monkeypatch):
    article = {
        "article_id": "a1",
        "source_id": "bbc_news",
        "url": "https://example.com/a1",
        "title": "Example article",
        "published_at": "2026-04-10T05:00:00Z",
        "raw_text_md": "Body",
    }
    analysis = {
        "article_summary_short": "Short summary",
        "entity_keys": ["entity_a"],
        "key_facts": ["fact_a"],
    }
    representative = {
        "article_id": "a1",
        "dedupe_group_id": "dedupe:a1",
        "duplicate_article_ids": [],
        "category_scores": {"geopolitics": 0.8, "europe": 0.5},
        "selection_score": 0.9,
        "editorial_status": "kept",
        "entity_keys": ["entity_a"],
    }
    config = SimpleNamespace(
        NEWS_CATEGORY_CONFIG=news_lane.default_news_category_config(),
        NEWS_BRIEF_MODEL="gemma",
        NEWS_ARTICLE_MODEL="gemma",
        NEWS_ARTICLE_MODEL_ENDPOINT="http://example.com",
        NEWS_BRIEF_MODEL_TIMEOUT_SECONDS=300,
    )
    captured = {}

    def _fake_completion(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return json.dumps(
            {
                "headline": "Заглавие",
                "what_happened": "Стана нещо важно.",
                "why_it_matters": "Има значение.",
                "paragraph": "Изречение едно. Изречение две. Изречение три. Изречение четири. Изречение пет. Изречение шест. Изречение седем.",
                "category_scores": {"geopolitics": 0.9},
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(news_lane, "_openai_compatible_chat_completion", _fake_completion)

    result = news_lane._summarize_brief_item_with_model(
        article,
        analysis,
        representative,
        config_or_path=config,
    )

    assert result is not None
    assert "7 to 9 sentences" in captured["user_prompt"]
    assert "140 to 260 words" in captured["user_prompt"]
    assert news_lane.NEWS_BRIEF_ITEM_PROMPT_VERSION == "news-brief-item-v3"


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


def test_builtin_news_consult_prefers_latest_briefing_for_general_request(news_fixture):
    payload = json.loads(
        asyncio.run(
            builtin_tools.news_consult(
                objective="Дай ми сутрешния news briefing и всичко от днес.",
                phase="start",
                __request__=news_fixture["request"],
            )
        )
    )

    assert payload["route"] == "latest_briefing"
    assert payload["snapshot_id"] == "20260410T060000Z"
    assert payload["selected_item_count"] == 1
    assert payload["latest_briefing"]["script"]
    assert payload["matched_stories"][0]["article_id"] == "a1"
    assert payload["source_documents"][0]["type"] == "news_briefing_item"
    assert payload["source_documents"][0]["content"].startswith(
        payload["matched_stories"][0]["paragraph"]
    )
    assert payload["response_contract"]["coverage_policy"] == "all_matched_stories_must_be_covered_once"
    assert payload["response_contract"]["output_shape"] == "one_block_per_story"
    assert payload["response_contract"]["allowed_merge_policy"] == "only_same_dedupe_group"
    assert payload["response_contract"]["required_story_count"] == 1
    assert payload["response_contract"]["required_story_ids"] == ["a1"]


def test_builtin_news_consult_builds_from_snapshot_when_briefing_missing(news_fixture):
    (news_fixture["briefings_root"] / "latest_briefing.json").unlink()
    (news_fixture["briefings_root"] / "2026-04-10" / "briefing.json").unlink()

    payload = json.loads(
        asyncio.run(
            builtin_tools.news_consult(
                objective="Give me the full morning briefing in English with all available detail.",
                phase="start",
                __request__=news_fixture["request"],
            )
        )
    )

    assert payload["route"] == "build_from_snapshot"
    assert payload["snapshot_id"] == "20260410T060000Z"
    assert payload["selected_item_count"] == 2
    assert payload["latest_briefing"]["ephemeral"] is True
    assert payload["latest_briefing"]["script"]
    assert payload["matched_stories"][0]["article_id"] == "a1"
    assert {item["article_id"] for item in payload["matched_stories"]} == {"a1", "a3"}
    assert payload["response_contract"]["required_story_count"] == 2
    assert set(payload["response_contract"]["required_story_ids"]) == {"a1", "a3"}


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


def test_select_full_brief_items_returns_all_kept_summary_ready_items(news_fixture):
    snapshot = news_lane.load_latest_closed_snapshot(news_fixture["config"])

    selected, audit = news_lane.select_full_brief_items(
        snapshot,
        config_or_path=news_fixture["config"],
    )

    assert [item["article_id"] for item in selected] == ["a1", "a3"]
    assert audit["geopolitics"]["selected_story_count"] >= 1
    assert audit["tech_ai"]["selected_story_count"] >= 1


def test_news_brief_target_item_count_enforces_full_brief_floor():
    config = SimpleNamespace(NEWS_BRIEF_TARGET_ITEM_COUNT=7)

    assert news_lane._news_brief_target_item_count(config) == 18


def test_load_latest_briefing(news_fixture):
    briefing = news_lane.load_latest_briefing(news_fixture["config"])

    assert briefing is not None
    assert briefing["snapshot_id"] == "20260410T060000Z"
    assert briefing["selected_items"][0]["article_id"] == "a1"


def test_build_briefing_uses_full_kept_brief_item_set(news_fixture):
    snapshot = news_lane.load_latest_closed_snapshot(news_fixture["config"])

    briefing = news_lane.build_briefing(
        config_or_path=news_fixture["config"],
        snapshot=snapshot,
    )

    assert briefing["target_item_count"] == 2
    assert briefing["configured_target_item_count"] == 18
    assert [item["article_id"] for item in briefing["selected_items"]] == ["a1", "a3"]


def test_general_briefing_request_detection():
    assert (
        news_lane._looks_like_general_briefing_request(
            objective="Дай ми всичко от днес в сутрешния briefing",
            phase="start",
        )
        is True
    )
    assert (
        news_lane._looks_like_general_briefing_request(
            objective="What is the latest on EU sanctions after the strike?",
            phase="start",
            named_entity="EU",
        )
        is False
    )


def test_builtin_news_consult_returns_graceful_empty_state_for_general_briefing(tmp_path):
    config = SimpleNamespace(
        ENABLE_LOCAL_CORPUS_TOOLS=False,
        LOCAL_CORPUS_ROOT="",
        OFFSEC_CORPUS_ROOT="",
        NEWS_ENABLED=True,
        NEWS_ARTICLE_STORE_ROOT=str(tmp_path / "news_articles"),
        NEWS_CORPUS_ROOT=str(tmp_path / "news_corpus"),
        NEWS_BRIEFINGS_ROOT=str(tmp_path / "news_briefings"),
        NEWS_SOURCE_REGISTRY=news_lane.default_news_source_registry(),
        NEWS_CATEGORY_CONFIG=news_lane.default_news_category_config(),
        NEWS_BRIEF_TARGET_ITEM_COUNT=12,
        NEWS_TTS_VOICE_ID="bg",
        TASK_MODEL="",
        TASK_MODEL_EXTERNAL=False,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))

    payload = json.loads(
        asyncio.run(
            builtin_tools.news_consult(
                objective="Дай ми сутрешния news briefing - не ми спестявай нищо.",
                phase="start",
                __request__=request,
            )
        )
    )

    assert payload["status"] == "empty"
    assert payload["reason"] == "no_closed_snapshot"
    assert payload["route"] == "empty_state"
    assert "няма затворен" in payload["message"].lower()


def _synthetic_brief_item(
    brief_item_id: str,
    *,
    source_id: str,
    selection_score: float,
    category_scores: dict[str, float],
    summary_status: str = "ok",
    topic_key: str | None = None,
):
    return {
        "brief_item_id": brief_item_id,
        "article_id": brief_item_id.replace("brief:", "article:"),
        "headline": brief_item_id,
        "title": brief_item_id,
        "what_happened": "what happened",
        "why_it_matters": "why it matters",
        "paragraph": "paragraph",
        "category_ids": [key for key, value in category_scores.items() if value > 0],
        "category_scores": category_scores,
        "selection_score": selection_score,
        "selected_for_brief": False,
        "summary_status": summary_status,
        "source_ref": {"source_id": source_id},
        "topic_key": topic_key or brief_item_id,
    }


def _best_category_for_item(item: dict[str, object], categories: list[dict[str, object]]):
    best_category = None
    best_score = None
    for category in categories:
        if not category.get("enabled"):
            continue
        score = news_lane.compute_story_candidate_score(item, category=category)
        if best_score is None or score["final_score"] > best_score["final_score"]:
            best_category = category
            best_score = score
    return best_category, best_score


def _simulate_editorial_allocator(
    items: list[dict[str, object]],
    *,
    categories: list[dict[str, object]],
    target_count: int = 12,
    per_category_min: int = 2,
    per_category_max: int = 4,
    eligibility_threshold: float = 0.75,
):
    enriched: list[dict[str, object]] = []
    for item in items:
        best_category, best_score = _best_category_for_item(item, categories)
        if best_category is None or best_score is None:
            continue
        enriched.append(
            {
                **item,
                "selected_category_id": best_category["category_id"],
                "selection_score_details": best_score,
                "allocator_score": best_score["final_score"],
            }
        )

    by_category: dict[str, list[dict[str, object]]] = {}
    for item in enriched:
        by_category.setdefault(item["selected_category_id"], []).append(item)
    for bucket in by_category.values():
        bucket.sort(key=lambda item: (-item["allocator_score"], item["brief_item_id"]))

    selected: list[dict[str, object]] = []
    selected_ids: set[str] = set()
    selected_topics: set[str] = set()
    counts_by_category: dict[str, int] = {}

    def maybe_add(item: dict[str, object]) -> bool:
        category_id = str(item["selected_category_id"])
        topic_key = str(item.get("topic_key") or item["brief_item_id"])
        if item["brief_item_id"] in selected_ids:
            return False
        if topic_key in selected_topics:
            return False
        if item["allocator_score"] < eligibility_threshold:
            return False
        if counts_by_category.get(category_id, 0) >= per_category_max:
            return False
        selected.append(item)
        selected_ids.add(item["brief_item_id"])
        selected_topics.add(topic_key)
        counts_by_category[category_id] = counts_by_category.get(category_id, 0) + 1
        return True

    for category in categories:
        if not category.get("enabled"):
            continue
        bucket = by_category.get(category["category_id"], [])
        eligible = [
            item for item in bucket if item["allocator_score"] >= eligibility_threshold
        ]
        reserve = min(
            per_category_max,
            len(eligible),
            per_category_min if len(eligible) >= per_category_min else len(eligible),
        )
        taken = 0
        for item in eligible:
            if taken >= reserve or len(selected) >= target_count:
                break
            if maybe_add(item):
                taken += 1

    remaining = sorted(
        [item for item in enriched if item["brief_item_id"] not in selected_ids],
        key=lambda item: (-item["allocator_score"], item["brief_item_id"]),
    )
    for item in remaining:
        if len(selected) >= target_count:
            break
        maybe_add(item)

    audit = {
        category["category_id"]: sum(
            1 for item in selected if item["selected_category_id"] == category["category_id"]
        )
        for category in categories
        if category.get("enabled")
    }
    return selected, audit


def test_selector_reserves_category_coverage_on_synthetic_heavy_lane():
    categories = news_lane.default_news_category_config()
    snapshot = {
        "snapshot_id": "synthetic-heavy-lane",
        "brief_items": [
            _synthetic_brief_item(
                "brief:tech-1",
                source_id="bbc_news",
                selection_score=0.96,
                category_scores={"tech_ai": 1.0, "geopolitics": 0.15},
                topic_key="tech-1",
            ),
            _synthetic_brief_item(
                "brief:tech-2",
                source_id="politico_europe",
                selection_score=0.91,
                category_scores={"tech_ai": 0.95, "europe": 0.20},
                topic_key="tech-2",
            ),
            _synthetic_brief_item(
                "brief:tech-3",
                source_id="bbc_news",
                selection_score=0.88,
                category_scores={"tech_ai": 0.93},
                topic_key="tech-3",
            ),
            _synthetic_brief_item(
                "brief:tech-4",
                source_id="politico_europe",
                selection_score=0.86,
                category_scores={"tech_ai": 0.90},
                topic_key="tech-4",
            ),
            _synthetic_brief_item(
                "brief:bg-1",
                source_id="svobodna_tochka_news",
                selection_score=0.81,
                category_scores={"bulgaria": 0.92, "geopolitics": 0.10},
                topic_key="bg-1",
            ),
            _synthetic_brief_item(
                "brief:bg-2",
                source_id="svobodna_tochka_news",
                selection_score=0.79,
                category_scores={"bulgaria": 0.88},
                topic_key="bg-2",
            ),
            _synthetic_brief_item(
                "brief:eu-1",
                source_id="politico_europe",
                selection_score=0.80,
                category_scores={"europe": 0.89, "economy": 0.30},
                topic_key="eu-1",
            ),
            _synthetic_brief_item(
                "brief:econ-1",
                source_id="bbc_news",
                selection_score=0.77,
                category_scores={"economy": 0.84},
                topic_key="econ-1",
            ),
            _synthetic_brief_item(
                "brief:econ-2",
                source_id="bbc_news",
                selection_score=0.72,
                category_scores={"economy": 0.79},
                topic_key="econ-2",
            ),
            _synthetic_brief_item(
                "brief:geo-1",
                source_id="bbc_news",
                selection_score=0.83,
                category_scores={"geopolitics": 0.90},
                topic_key="geo-1",
            ),
            _synthetic_brief_item(
                "brief:geo-2",
                source_id="politico_europe",
                selection_score=0.78,
                category_scores={"geopolitics": 0.86, "europe": 0.22},
                topic_key="geo-2",
            ),
            _synthetic_brief_item(
                "brief:weird-1",
                source_id="bbc_news",
                selection_score=0.68,
                category_scores={"weird": 0.80},
                topic_key="weird-1",
            ),
        ],
    }

    config = SimpleNamespace(
        NEWS_CATEGORY_CONFIG=categories,
        NEWS_BRIEF_TARGET_ITEM_COUNT=7,
    )

    selected, audit = news_lane.select_stories_by_categories(
        snapshot,
        config_or_path=config,
    )

    selected_categories = [item["selected_category_id"] for item in selected]

    assert len(selected) >= 7
    assert selected_categories.count("tech_ai") <= 4
    assert audit["bulgaria"]["selected_story_count"] >= 1
    assert audit["economy"]["selected_story_count"] >= 1
    assert audit["geopolitics"]["selected_story_count"] >= 1


def test_synthetic_allocator_reserves_two_per_category_when_eligible():
    categories = news_lane.default_news_category_config()
    items = [
        _synthetic_brief_item(
            "brief:geo-1",
            source_id="bbc_news",
            selection_score=0.90,
            category_scores={"geopolitics": 0.95},
            topic_key="geo-1",
        ),
        _synthetic_brief_item(
            "brief:geo-2",
            source_id="politico_europe",
            selection_score=0.86,
            category_scores={"geopolitics": 0.90},
            topic_key="geo-2",
        ),
        _synthetic_brief_item(
            "brief:econ-1",
            source_id="bbc_news",
            selection_score=0.88,
            category_scores={"economy": 0.92},
            topic_key="econ-1",
        ),
        _synthetic_brief_item(
            "brief:econ-2",
            source_id="politico_europe",
            selection_score=0.83,
            category_scores={"economy": 0.87},
            topic_key="econ-2",
        ),
        _synthetic_brief_item(
            "brief:tech-1",
            source_id="bbc_news",
            selection_score=0.98,
            category_scores={"tech_ai": 0.99},
            topic_key="tech-1",
        ),
        _synthetic_brief_item(
            "brief:tech-2",
            source_id="politico_europe",
            selection_score=0.94,
            category_scores={"tech_ai": 0.95},
            topic_key="tech-2",
        ),
        _synthetic_brief_item(
            "brief:eu-1",
            source_id="politico_europe",
            selection_score=0.89,
            category_scores={"europe": 0.94},
            topic_key="eu-1",
        ),
        _synthetic_brief_item(
            "brief:eu-2",
            source_id="bbc_news",
            selection_score=0.84,
            category_scores={"europe": 0.88},
            topic_key="eu-2",
        ),
        _synthetic_brief_item(
            "brief:bg-1",
            source_id="svobodna_tochka_news",
            selection_score=0.91,
            category_scores={"bulgaria": 0.96},
            topic_key="bg-1",
        ),
        _synthetic_brief_item(
            "brief:bg-2",
            source_id="svobodna_tochka_news",
            selection_score=0.85,
            category_scores={"bulgaria": 0.90},
            topic_key="bg-2",
        ),
        _synthetic_brief_item(
            "brief:weird-1",
            source_id="bbc_news",
            selection_score=0.87,
            category_scores={"weird": 0.93},
            topic_key="weird-1",
        ),
        _synthetic_brief_item(
            "brief:weird-2",
            source_id="politico_europe",
            selection_score=0.81,
            category_scores={"weird": 0.86},
            topic_key="weird-2",
        ),
    ]

    selected, audit = _simulate_editorial_allocator(
        items,
        categories=categories,
        target_count=12,
        per_category_min=2,
        per_category_max=4,
        eligibility_threshold=0.75,
    )

    assert len(selected) == 12
    assert audit["geopolitics"] >= 2
    assert audit["economy"] >= 2
    assert audit["tech_ai"] >= 2
    assert audit["europe"] >= 2
    assert audit["bulgaria"] >= 2
    assert audit["weird"] >= 2


def test_synthetic_allocator_caps_single_lane_and_skips_redundant_topics():
    categories = news_lane.default_news_category_config()
    items = [
        _synthetic_brief_item(
            "brief:tech-1",
            source_id="bbc_news",
            selection_score=0.97,
            category_scores={"tech_ai": 0.99},
            topic_key="openai-launch",
        ),
        _synthetic_brief_item(
            "brief:tech-2",
            source_id="politico_europe",
            selection_score=0.96,
            category_scores={"tech_ai": 0.98},
            topic_key="openai-launch",
        ),
        _synthetic_brief_item(
            "brief:tech-3",
            source_id="bbc_news",
            selection_score=0.95,
            category_scores={"tech_ai": 0.97},
            topic_key="chip-export",
        ),
        _synthetic_brief_item(
            "brief:tech-4",
            source_id="politico_europe",
            selection_score=0.94,
            category_scores={"tech_ai": 0.96},
            topic_key="model-benchmark",
        ),
        _synthetic_brief_item(
            "brief:tech-5",
            source_id="bbc_news",
            selection_score=0.93,
            category_scores={"tech_ai": 0.95},
            topic_key="datacenter-power",
        ),
        _synthetic_brief_item(
            "brief:bg-1",
            source_id="svobodna_tochka_news",
            selection_score=0.84,
            category_scores={"bulgaria": 0.92},
            topic_key="bg-1",
        ),
        _synthetic_brief_item(
            "brief:bg-2",
            source_id="svobodna_tochka_news",
            selection_score=0.82,
            category_scores={"bulgaria": 0.89},
            topic_key="bg-2",
        ),
        _synthetic_brief_item(
            "brief:geo-1",
            source_id="bbc_news",
            selection_score=0.86,
            category_scores={"geopolitics": 0.91},
            topic_key="geo-1",
        ),
        _synthetic_brief_item(
            "brief:geo-2",
            source_id="politico_europe",
            selection_score=0.83,
            category_scores={"geopolitics": 0.87},
            topic_key="geo-2",
        ),
    ]

    selected, audit = _simulate_editorial_allocator(
        items,
        categories=categories,
        target_count=8,
        per_category_min=2,
        per_category_max=4,
        eligibility_threshold=0.75,
    )

    selected_ids = {item["brief_item_id"] for item in selected}

    assert len(selected) == 8
    assert audit["tech_ai"] <= 4
    assert not {"brief:tech-1", "brief:tech-2"} <= selected_ids


def test_synthetic_allocator_does_not_force_low_quality_category_fill():
    categories = news_lane.default_news_category_config()
    items = [
        _synthetic_brief_item(
            "brief:geo-1",
            source_id="bbc_news",
            selection_score=0.89,
            category_scores={"geopolitics": 0.94},
            topic_key="geo-1",
        ),
        _synthetic_brief_item(
            "brief:geo-2",
            source_id="politico_europe",
            selection_score=0.84,
            category_scores={"geopolitics": 0.89},
            topic_key="geo-2",
        ),
        _synthetic_brief_item(
            "brief:bg-1",
            source_id="svobodna_tochka_news",
            selection_score=0.87,
            category_scores={"bulgaria": 0.93},
            topic_key="bg-1",
        ),
        _synthetic_brief_item(
            "brief:bg-2",
            source_id="svobodna_tochka_news",
            selection_score=0.81,
            category_scores={"bulgaria": 0.88},
            topic_key="bg-2",
        ),
        _synthetic_brief_item(
            "brief:weird-weak",
            source_id="bbc_news",
            selection_score=0.40,
            category_scores={"weird": 0.45},
            topic_key="weird-weak",
        ),
    ]

    selected, audit = _simulate_editorial_allocator(
        items,
        categories=categories,
        target_count=6,
        per_category_min=2,
        per_category_max=4,
        eligibility_threshold=0.75,
    )

    assert audit["weird"] == 0
    assert all(item["brief_item_id"] != "brief:weird-weak" for item in selected)


def test_parse_datetime_supports_rss_pubdate():
    parsed = news_lane._parse_datetime("Mon, 06 Apr 2026 12:31:31 GMT")

    assert parsed is not None
    assert parsed.isoformat() == "2026-04-06T12:31:31+00:00"


def test_discover_and_fetch_news_skips_entries_older_than_24_hours(monkeypatch, tmp_path):
    now = news_lane._parse_datetime("2026-04-11T13:00:00Z")
    assert now is not None

    config = SimpleNamespace(
        NEWS_ARTICLE_STORE_ROOT=str(tmp_path / "news_articles"),
        NEWS_CORPUS_ROOT=str(tmp_path / "news_corpus"),
        NEWS_BRIEFINGS_ROOT=str(tmp_path / "news_briefings"),
        NEWS_SOURCE_REGISTRY=[
            {
                "source_id": "ars_ai",
                "label": "Ars AI",
                "adapter_type": "rss_atom",
                "enabled": True,
                "seed_urls": ["https://arstechnica.com/ai/feed/"],
                "language": "en",
                "region_tags": ["global"],
                "topic_tags": ["tech_ai"],
            }
        ],
    )

    monkeypatch.setattr(news_lane, "_utc_now", lambda: now)
    monkeypatch.setattr(
        news_lane,
        "_discover_source_entries",
        lambda source: [
            {
                "title": "Fresh AI item",
                "url": "https://example.com/fresh",
                "excerpt": "fresh",
                "published_at": "Fri, 10 Apr 2026 14:00:00 GMT",
                "source_id": source["source_id"],
                "discovery_metadata": {"adapter_type": source["adapter_type"]},
            },
            {
                "title": "Old AI item",
                "url": "https://example.com/old",
                "excerpt": "old",
                "published_at": "Mon, 06 Apr 2026 12:31:31 GMT",
                "source_id": source["source_id"],
                "discovery_metadata": {"adapter_type": source["adapter_type"]},
            },
        ],
    )
    monkeypatch.setattr(
        news_lane,
        "_fetch_article_body",
        lambda seed: (seed["title"], "A" * 200),
    )

    result = news_lane.discover_and_fetch_news(config_or_path=config)

    assert result["discovered_total"] == 2
    assert len(result["fetched_article_ids"]) == 1

    article_files = list((tmp_path / "news_articles" / "articles").glob("*/article.json"))
    assert len(article_files) == 1
    payload = json.loads(article_files[0].read_text(encoding="utf-8"))
    assert payload["url"] == "https://example.com/fresh"


def test_normalize_news_wake_time_accepts_and_zero_pads():
    assert news_router.normalize_news_wake_time("5:8") == "05:08"
    assert news_router.normalize_news_wake_time("05:08") == "05:08"


def test_normalize_news_wake_time_defaults_when_empty():
    assert news_router.normalize_news_wake_time("") == "05:08"
    assert news_router.normalize_news_wake_time(None) == "05:08"


def test_normalize_news_wake_time_rejects_invalid_values():
    with pytest.raises(ValueError):
        news_router.normalize_news_wake_time("25:00")

    with pytest.raises(ValueError):
        news_router.normalize_news_wake_time("abc")


def test_run_news_morning_pipeline_chains_snapshot_and_briefing(monkeypatch):
    captured = {}

    def fake_snapshot_pipeline(config_or_path):
        captured["snapshot_config"] = config_or_path
        return {
            "fetch": {"fetched_article_ids": ["a1"]},
            "prefetch": {"prefetched_article_ids": []},
            "analysis": {"analyzed_article_ids": ["a1"]},
            "snapshot": {"snapshot_id": "snap-1"},
        }

    def fake_build_briefing(config_or_path, snapshot):
        captured["briefing_config"] = config_or_path
        captured["briefing_snapshot"] = snapshot
        return {"briefing_id": "brief-1"}

    monkeypatch.setattr(news_router, "run_news_snapshot_pipeline", fake_snapshot_pipeline)
    monkeypatch.setattr(news_router, "build_briefing", fake_build_briefing)

    config = SimpleNamespace(NEWS_ENABLED=True)
    result = news_router.run_news_morning_pipeline(config)

    assert result["snapshot"]["snapshot_id"] == "snap-1"
    assert result["briefing"]["briefing_id"] == "brief-1"
    assert captured["snapshot_config"] is config
    assert captured["briefing_config"] is config
    assert captured["briefing_snapshot"] == {"snapshot_id": "snap-1"}
