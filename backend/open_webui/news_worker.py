import json
from types import SimpleNamespace

import typer

from open_webui.config import (
    NEWS_ARTICLE_MODEL,
    NEWS_ARTICLE_MODEL_ENDPOINT,
    NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS,
    NEWS_ARTICLE_STORE_ROOT,
    NEWS_BRIEFINGS_ROOT,
    NEWS_BRIEF_MODEL,
    NEWS_BRIEF_MODEL_TIMEOUT_SECONDS,
    NEWS_CATEGORY_CONFIG,
    NEWS_CORPUS_ROOT,
    NEWS_ENABLED,
    NEWS_PLAYBACK_DEVICE,
    NEWS_SOURCE_REGISTRY,
    NEWS_TTS_VOICE_ID,
    NEWS_WAKE_TIME,
)
from open_webui.retrieval.news_lane import (
    analyze_articles,
    build_briefing,
    build_snapshot,
    discover_and_fetch_news,
    load_latest_closed_snapshot,
    play_latest_briefing,
    prefetch_related_once,
)

app = typer.Typer(help="Ariadne News lane background worker")


def _value(item):
    return getattr(item, "value", item)


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        NEWS_ENABLED=bool(_value(NEWS_ENABLED)),
        NEWS_ARTICLE_STORE_ROOT=str(_value(NEWS_ARTICLE_STORE_ROOT)),
        NEWS_CORPUS_ROOT=str(_value(NEWS_CORPUS_ROOT)),
        NEWS_BRIEFINGS_ROOT=str(_value(NEWS_BRIEFINGS_ROOT)),
        NEWS_ARTICLE_MODEL_ENDPOINT=str(_value(NEWS_ARTICLE_MODEL_ENDPOINT)),
        NEWS_ARTICLE_MODEL=str(_value(NEWS_ARTICLE_MODEL)),
        NEWS_BRIEF_MODEL=str(_value(NEWS_BRIEF_MODEL)),
        NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS=int(_value(NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS)),
        NEWS_BRIEF_MODEL_TIMEOUT_SECONDS=int(_value(NEWS_BRIEF_MODEL_TIMEOUT_SECONDS)),
        NEWS_TTS_VOICE_ID=str(_value(NEWS_TTS_VOICE_ID)),
        NEWS_WAKE_TIME=str(_value(NEWS_WAKE_TIME)),
        NEWS_PLAYBACK_DEVICE=str(_value(NEWS_PLAYBACK_DEVICE)),
        NEWS_SOURCE_REGISTRY=_value(NEWS_SOURCE_REGISTRY),
        NEWS_CATEGORY_CONFIG=_value(NEWS_CATEGORY_CONFIG),
    )


def _echo(payload):
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("ingest")
def ingest():
    _echo(discover_and_fetch_news(config_or_path=_config()))


@app.command("prefetch")
def prefetch():
    fetch = discover_and_fetch_news(config_or_path=_config())
    _echo(
        prefetch_related_once(
            config_or_path=_config(),
            article_ids=fetch.get("fetched_article_ids", []),
        )
    )


@app.command("analyze")
def analyze():
    fetch = discover_and_fetch_news(config_or_path=_config())
    _echo(
        analyze_articles(
            config_or_path=_config(),
            article_ids=fetch.get("fetched_article_ids", []),
        )
    )


@app.command("compile")
def compile_snapshot():
    fetch = discover_and_fetch_news(config_or_path=_config())
    prefetch = prefetch_related_once(
        config_or_path=_config(),
        article_ids=fetch.get("fetched_article_ids", []),
    )
    analyze_articles(
        config_or_path=_config(),
        article_ids=fetch.get("fetched_article_ids", []),
    )
    _echo(
        build_snapshot(
            config_or_path=_config(),
            article_ids=fetch.get("fetched_article_ids", []),
            prefetched_article_ids=prefetch.get("prefetched_article_ids", []),
        )
    )


@app.command("brief")
def brief():
    snapshot = load_latest_closed_snapshot(_config())
    _echo(build_briefing(config_or_path=_config(), snapshot=snapshot))


@app.command("play-latest")
def play_latest():
    _echo(play_latest_briefing(config_or_path=_config()))


@app.command("run-hourly")
def run_hourly():
    fetch = discover_and_fetch_news(config_or_path=_config())
    prefetch = prefetch_related_once(
        config_or_path=_config(),
        article_ids=fetch.get("fetched_article_ids", []),
    )
    analysis = analyze_articles(
        config_or_path=_config(),
        article_ids=fetch.get("fetched_article_ids", []),
    )
    snapshot = build_snapshot(
        config_or_path=_config(),
        article_ids=fetch.get("fetched_article_ids", []),
        prefetched_article_ids=prefetch.get("prefetched_article_ids", []),
    )
    _echo(
        {
            "fetch": fetch,
            "prefetch": prefetch,
            "analysis": analysis,
            "snapshot": snapshot,
        }
    )


@app.command("run-daily")
def run_daily():
    snapshot = load_latest_closed_snapshot(_config())
    briefing = build_briefing(config_or_path=_config(), snapshot=snapshot)
    _echo({"briefing": briefing})
