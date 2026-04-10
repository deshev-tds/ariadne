from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from open_webui.env import BASE_DIR
from open_webui.retrieval.local_corpus import resolve_repo_relative_corpus_root

log = logging.getLogger(__name__)

DEFAULT_NEWS_ARTICLE_STORE_ROOT_SETTING = Path("news_articles")
DEFAULT_NEWS_CORPUS_ROOT_SETTING = Path("news_corpus")
DEFAULT_NEWS_BRIEFINGS_ROOT_SETTING = Path("news_briefings")
NEWS_SOURCE_REGISTRY_VERSION = 1
NEWS_CATEGORY_CONFIG_VERSION = 1
NEWS_COMPILER_VERSION = "news-v1.5"
NEWS_ANALYZER_PROMPT_VERSION = "news-analysis-v1"
NEWS_BRIEF_PROMPT_VERSION = "news-brief-v1"
NEWS_PREFERRED_SOURCE_BONUS_CAP = 0.08
DEFAULT_NEWS_MODEL_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS = 300
DEFAULT_NEWS_BRIEF_MODEL_TIMEOUT_SECONDS = 300
NEWS_SUPPORTED_CLAIM_KINDS = (
    "count",
    "status",
    "decision",
    "date_time",
    "official_position",
)
NEWS_MAX_EVIDENCE_SPANS_PER_ARTICLE = 10

NEWS_STOP_TERMS = {
    "a",
    "about",
    "after",
    "again",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "more",
    "new",
    "of",
    "on",
    "or",
    "over",
    "says",
    "saying",
    "said",
    "that",
    "the",
    "their",
    "this",
    "to",
    "under",
    "was",
    "were",
    "will",
    "with",
}
NEWS_CONFLICT_STATUS_TERMS = {
    "approved",
    "blocked",
    "cancelled",
    "confirmed",
    "delayed",
    "denied",
    "failed",
    "paused",
    "rejected",
    "signed",
}
NEWS_CONTEXT_TERMS = {
    "again",
    "after",
    "against",
    "amid",
    "before",
    "despite",
    "escalation",
    "following",
    "latest",
    "meanwhile",
    "renewed",
    "response",
    "retaliation",
    "timeline",
    "update",
}
NEWS_DOMAIN_KEYWORDS = {
    "geopolitics": {
        "war",
        "conflict",
        "military",
        "missile",
        "attack",
        "sanctions",
        "nato",
        "ukraine",
        "russia",
        "israel",
        "gaza",
        "iran",
        "china",
        "taiwan",
        "diplomacy",
    },
    "economy": {
        "economy",
        "inflation",
        "market",
        "growth",
        "recession",
        "trade",
        "rates",
        "bank",
        "stocks",
        "bond",
        "jobs",
        "tariff",
        "oil",
        "gas",
    },
    "tech_ai": {
        "ai",
        "model",
        "llm",
        "openai",
        "anthropic",
        "meta",
        "gpu",
        "chip",
        "hacker",
        "startup",
        "arxiv",
        "hugging",
        "transformer",
        "robot",
        "software",
    },
    "europe": {
        "europe",
        "eu",
        "european",
        "brussels",
        "commission",
        "parliament",
        "germany",
        "france",
        "poland",
        "italy",
    },
    "bulgaria": {
        "bulgaria",
        "bulgarian",
        "sofia",
        "капитал",
        "дневник",
        "българ",
        "софия",
        "парламент",
        "правителство",
    },
    "weird": {
        "weird",
        "bizarre",
        "odd",
        "strange",
        "unexpected",
        "mystery",
        "anomaly",
        "ufo",
        "impossible",
        "viral",
    },
}


@dataclass(frozen=True)
class NewsRoots:
    article_store_root: Path
    corpus_root: Path
    briefings_root: Path


def _config_value(config_or_path: Any, key: str, default: Any = None) -> Any:
    if config_or_path is None:
        return default
    raw = getattr(config_or_path, key, default)
    if hasattr(raw, "value"):
        return getattr(raw, "value")
    return raw


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _normalize_token_set(values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    if not values:
        return []
    normalized = {
        _normalize_text(item).lower()
        for item in values
        if _normalize_text(item)
    }
    return sorted(normalized)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc)
            if value.tzinfo is not None
            else value.replace(tzinfo=timezone.utc)
        )

    raw = _normalize_text(value)
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def default_news_source_registry() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "reuters",
            "label": "Reuters",
            "adapter_type": "html_headlines",
            "enabled": True,
            "seed_urls": ["https://www.reuters.com/world/", "https://www.reuters.com/business/"],
            "language": "en",
            "region_tags": ["global"],
            "topic_tags": ["geopolitics", "economy"],
        },
        {
            "source_id": "bbc_news",
            "label": "BBC News",
            "adapter_type": "rss_atom",
            "enabled": True,
            "seed_urls": ["https://feeds.bbci.co.uk/news/world/rss.xml"],
            "language": "en",
            "region_tags": ["global", "europe"],
            "topic_tags": ["geopolitics", "europe"],
        },
        {
            "source_id": "politico_europe",
            "label": "Politico Europe",
            "adapter_type": "rss_atom",
            "enabled": True,
            "seed_urls": ["https://www.politico.eu/category/europe/feed/"],
            "language": "en",
            "region_tags": ["europe"],
            "topic_tags": ["europe", "geopolitics", "economy"],
        },
        {
            "source_id": "kapital",
            "label": "Капитал",
            "adapter_type": "html_headlines",
            "enabled": True,
            "seed_urls": ["https://www.capital.bg/"],
            "language": "bg",
            "region_tags": ["bulgaria"],
            "topic_tags": ["bulgaria", "economy"],
        },
        {
            "source_id": "dnevnik",
            "label": "Дневник",
            "adapter_type": "html_headlines",
            "enabled": True,
            "seed_urls": ["https://www.dnevnik.bg/"],
            "language": "bg",
            "region_tags": ["bulgaria"],
            "topic_tags": ["bulgaria", "europe"],
        },
        {
            "source_id": "hacker_news",
            "label": "Hacker News",
            "adapter_type": "hn_top",
            "enabled": True,
            "seed_urls": [],
            "language": "en",
            "region_tags": ["global"],
            "topic_tags": ["tech_ai"],
        },
        {
            "source_id": "arxiv_ai",
            "label": "arXiv AI/ML",
            "adapter_type": "arxiv_api",
            "enabled": True,
            "seed_urls": [],
            "language": "en",
            "region_tags": ["global"],
            "topic_tags": ["tech_ai"],
        },
        {
            "source_id": "huggingface_blog",
            "label": "Hugging Face",
            "adapter_type": "html_headlines",
            "enabled": True,
            "seed_urls": ["https://huggingface.co/blog"],
            "language": "en",
            "region_tags": ["global"],
            "topic_tags": ["tech_ai"],
        },
        {
            "source_id": "ground_news",
            "label": "Ground News",
            "adapter_type": "html_headlines",
            "enabled": False,
            "seed_urls": ["https://ground.news/"],
            "language": "en",
            "region_tags": ["global"],
            "topic_tags": ["geopolitics", "economy"],
        },
    ]


def default_news_category_config() -> list[dict[str, Any]]:
    return [
        {
            "category_id": "geopolitics",
            "label": "Geopolitics",
            "help_text": "Global geopolitical developments, conflicts and diplomacy.",
            "enabled": True,
            "display_order": 10,
            "target_slots": 1,
            "assignment_terms": [
                "war",
                "conflict",
                "diplomacy",
                "sanctions",
                "security",
                "military",
                "geopolitics",
            ],
            "preferred_source_ids": ["reuters", "bbc_news", "politico_europe"],
        },
        {
            "category_id": "economy",
            "label": "Economy",
            "help_text": "Macro, trade, markets and infrastructure-relevant business moves.",
            "enabled": True,
            "display_order": 20,
            "target_slots": 1,
            "assignment_terms": [
                "economy",
                "inflation",
                "market",
                "trade",
                "rates",
                "bank",
                "jobs",
                "oil",
            ],
            "preferred_source_ids": ["reuters", "kapital"],
        },
        {
            "category_id": "tech_ai",
            "label": "Tech / AI",
            "help_text": "AI, infrastructure, chips, models, notable software releases.",
            "enabled": True,
            "display_order": 30,
            "target_slots": 2,
            "assignment_terms": [
                "ai",
                "model",
                "llm",
                "chip",
                "gpu",
                "research",
                "software",
                "startup",
            ],
            "preferred_source_ids": ["hacker_news", "arxiv_ai", "huggingface_blog", "reuters"],
        },
        {
            "category_id": "europe",
            "label": "Europe",
            "help_text": "EU institutions and major European developments.",
            "enabled": True,
            "display_order": 40,
            "target_slots": 1,
            "assignment_terms": [
                "europe",
                "european",
                "eu",
                "commission",
                "parliament",
                "brussels",
            ],
            "preferred_source_ids": ["politico_europe", "bbc_news"],
        },
        {
            "category_id": "bulgaria",
            "label": "Bulgaria",
            "help_text": "Bulgarian politics, economy and local signal worth hearing.",
            "enabled": True,
            "display_order": 50,
            "target_slots": 1,
            "assignment_terms": [
                "bulgaria",
                "bulgarian",
                "sofia",
                "правителство",
                "парламент",
                "българия",
                "софия",
            ],
            "preferred_source_ids": ["kapital", "dnevnik"],
        },
        {
            "category_id": "weird",
            "label": "Weird",
            "help_text": "Low-volume anomaly lane for stories that are strange but still worth a slot.",
            "enabled": True,
            "display_order": 60,
            "target_slots": 1,
            "assignment_terms": [
                "weird",
                "odd",
                "bizarre",
                "unexpected",
                "anomaly",
                "strange",
            ],
            "preferred_source_ids": [],
        },
    ]


def normalize_news_source_registry_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Source registry must be a list")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for raw_entry in payload:
        if not isinstance(raw_entry, dict):
            raise ValueError("Each source entry must be an object")

        source_id = _normalize_slug(raw_entry.get("source_id") or raw_entry.get("label"))
        if not source_id:
            raise ValueError("Each source entry requires source_id or label")
        if source_id in seen_ids:
            raise ValueError(f"Duplicate source_id: {source_id}")
        seen_ids.add(source_id)

        adapter_type = _normalize_slug(raw_entry.get("adapter_type") or "rss_atom")
        seed_urls = [
            _normalize_text(url)
            for url in list(raw_entry.get("seed_urls") or [])
            if _normalize_text(url)
        ]
        if adapter_type == "rss_atom" and not seed_urls:
            raise ValueError(f"rss_atom source '{source_id}' requires at least one feed URL")

        normalized.append(
            {
                "source_id": source_id,
                "label": _normalize_text(raw_entry.get("label") or source_id.replace("_", " ").title()),
                "adapter_type": adapter_type,
                "enabled": bool(raw_entry.get("enabled", True)),
                "seed_urls": seed_urls,
                "language": _normalize_text(raw_entry.get("language") or "en").lower() or "en",
                "region_tags": _normalize_token_set(raw_entry.get("region_tags")),
                "topic_tags": _normalize_token_set(raw_entry.get("topic_tags")),
            }
        )

    return normalized


def validate_news_source_registry_payload(payload: Any) -> dict[str, Any]:
    registry = normalize_news_source_registry_payload(payload)
    return {
        "version": NEWS_SOURCE_REGISTRY_VERSION,
        "source_count": len(registry),
        "enabled_source_count": sum(1 for item in registry if item.get("enabled")),
        "adapter_types": sorted({item.get("adapter_type") for item in registry}),
    }


def normalize_news_category_config_payload(
    payload: Any, *, source_registry: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Category config must be a list")

    registry = source_registry if source_registry is not None else default_news_source_registry()
    known_sources = {item["source_id"] for item in normalize_news_source_registry_payload(registry)}

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for raw_entry in payload:
        if not isinstance(raw_entry, dict):
            raise ValueError("Each category entry must be an object")

        category_id = _normalize_slug(raw_entry.get("category_id") or raw_entry.get("label"))
        if not category_id:
            raise ValueError("Each category entry requires category_id or label")
        if category_id in seen_ids:
            raise ValueError(f"Duplicate category_id: {category_id}")
        seen_ids.add(category_id)

        preferred_source_ids = _normalize_token_set(raw_entry.get("preferred_source_ids"))
        unknown_sources = [item for item in preferred_source_ids if item not in known_sources]
        if unknown_sources:
            raise ValueError(
                f"Category '{category_id}' references unknown preferred sources: {', '.join(unknown_sources)}"
            )

        target_slots = int(raw_entry.get("target_slots", 0) or 0)
        if target_slots < 0:
            raise ValueError(f"Category '{category_id}' target_slots must be >= 0")

        normalized.append(
            {
                "category_id": category_id,
                "label": _normalize_text(raw_entry.get("label") or category_id.replace("_", " ").title()),
                "help_text": _normalize_text(raw_entry.get("help_text")),
                "enabled": bool(raw_entry.get("enabled", True)),
                "display_order": int(raw_entry.get("display_order", 0) or 0),
                "target_slots": target_slots,
                "assignment_terms": _normalize_token_set(raw_entry.get("assignment_terms")),
                "preferred_source_ids": preferred_source_ids,
            }
        )

    return normalized


def validate_news_category_config_payload(
    payload: Any, *, source_registry: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    categories = normalize_news_category_config_payload(payload, source_registry=source_registry)
    return {
        "version": NEWS_CATEGORY_CONFIG_VERSION,
        "category_count": len(categories),
        "enabled_category_count": sum(1 for item in categories if item.get("enabled")),
        "target_slots_total": sum(int(item.get("target_slots", 0) or 0) for item in categories),
    }


def news_source_registry_semantic_hash(payload: Any) -> str:
    registry = normalize_news_source_registry_payload(payload)
    semantic = sorted(
        [
            {
                "source_id": item["source_id"],
                "enabled": bool(item["enabled"]),
                "adapter_type": item["adapter_type"],
                "seed_urls": sorted(item["seed_urls"]),
                "region_tags": sorted(item["region_tags"]),
                "topic_tags": sorted(item["topic_tags"]),
            }
            for item in registry
        ],
        key=lambda item: item["source_id"],
    )
    return _sha256_text(_canonical_json(semantic))


def news_category_config_semantic_hash(
    payload: Any, *, source_registry: list[dict[str, Any]] | None = None
) -> str:
    categories = normalize_news_category_config_payload(payload, source_registry=source_registry)
    semantic = sorted(
        [
            {
                "category_id": item["category_id"],
                "enabled": bool(item["enabled"]),
                "target_slots": int(item["target_slots"]),
                "assignment_terms": sorted(item["assignment_terms"]),
                "preferred_source_ids": sorted(item["preferred_source_ids"]),
            }
            for item in categories
        ],
        key=lambda item: item["category_id"],
    )
    return _sha256_text(_canonical_json(semantic))


def load_news_source_registry(config_or_path: Any = None) -> list[dict[str, Any]]:
    payload = _config_value(config_or_path, "NEWS_SOURCE_REGISTRY", default_news_source_registry())
    return normalize_news_source_registry_payload(payload)


def load_news_category_config(config_or_path: Any = None) -> list[dict[str, Any]]:
    registry = load_news_source_registry(config_or_path)
    payload = _config_value(config_or_path, "NEWS_CATEGORY_CONFIG", default_news_category_config())
    return normalize_news_category_config_payload(payload, source_registry=registry)


def resolve_news_article_store_root(config_or_path: Any = None) -> Path:
    raw = _config_value(
        config_or_path,
        "NEWS_ARTICLE_STORE_ROOT",
        DEFAULT_NEWS_ARTICLE_STORE_ROOT_SETTING,
    )
    resolved = resolve_repo_relative_corpus_root(
        Path(str(raw)),
        DEFAULT_NEWS_ARTICLE_STORE_ROOT_SETTING,
    )
    return resolved or (BASE_DIR / Path(str(raw))).expanduser().resolve()


def resolve_news_corpus_root(config_or_path: Any = None) -> Path:
    raw = _config_value(
        config_or_path,
        "NEWS_CORPUS_ROOT",
        DEFAULT_NEWS_CORPUS_ROOT_SETTING,
    )
    resolved = resolve_repo_relative_corpus_root(
        Path(str(raw)),
        DEFAULT_NEWS_CORPUS_ROOT_SETTING,
    )
    return resolved or (BASE_DIR / Path(str(raw))).expanduser().resolve()


def resolve_news_briefings_root(config_or_path: Any = None) -> Path:
    raw = _config_value(
        config_or_path,
        "NEWS_BRIEFINGS_ROOT",
        DEFAULT_NEWS_BRIEFINGS_ROOT_SETTING,
    )
    resolved = resolve_repo_relative_corpus_root(
        Path(str(raw)),
        DEFAULT_NEWS_BRIEFINGS_ROOT_SETTING,
    )
    return resolved or (BASE_DIR / Path(str(raw))).expanduser().resolve()


def resolve_news_roots(config_or_path: Any = None) -> NewsRoots:
    article_store_root = resolve_news_article_store_root(config_or_path)
    corpus_root = resolve_news_corpus_root(config_or_path)
    briefings_root = resolve_news_briefings_root(config_or_path)
    article_store_root.mkdir(parents=True, exist_ok=True)
    corpus_root.mkdir(parents=True, exist_ok=True)
    briefings_root.mkdir(parents=True, exist_ok=True)
    return NewsRoots(
        article_store_root=article_store_root,
        corpus_root=corpus_root,
        briefings_root=briefings_root,
    )


def _article_dir(article_store_root: Path, article_id: str) -> Path:
    return article_store_root / "articles" / article_id


def _sentence_index(text: str) -> list[dict[str, Any]]:
    sentence_re = re.compile(r"[^.!?\n]+[.!?]?|\n+")
    sentences: list[dict[str, Any]] = []
    counter = 0
    for match in sentence_re.finditer(text):
        sentence = _normalize_text(match.group(0))
        if len(sentence) < 20:
            continue
        counter += 1
        sentences.append(
            {
                "sentence_id": f"s{counter}",
                "text": sentence,
                "char_start": int(match.start()),
                "char_end": int(match.end()),
            }
        )
    return sentences


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_article_record(article_store_root: Path, article_id: str) -> dict[str, Any]:
    return _read_json(_article_dir(article_store_root, article_id) / "article.json")


def _load_analysis_record(article_store_root: Path, article_id: str) -> Optional[dict[str, Any]]:
    path = _article_dir(article_store_root, article_id) / "analysis.json"
    if not path.exists():
        return None
    return _read_json(path)


def _list_article_ids(article_store_root: Path) -> list[str]:
    articles_root = article_store_root / "articles"
    if not articles_root.exists():
        return []
    return sorted(path.name for path in articles_root.iterdir() if path.is_dir())


def _discover_seed_requests(source: dict[str, Any]) -> list[dict[str, Any]]:
    adapter_type = source.get("adapter_type")
    if adapter_type in {"rss_atom", "html_headlines"}:
        return [{"url": url, "source": source} for url in source.get("seed_urls", [])]
    return [{"url": None, "source": source}]


def _request_get(url: str, *, timeout: int = 20) -> requests.Response:
    return requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "AriadneNewsLane/1.5 (+https://openwebui.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )


def _parse_rss_or_atom(feed_text: str, base_url: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    root = ElementTree.fromstring(feed_text)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }
    if root.tag.endswith("rss") or root.tag.endswith("RDF"):
        for item in root.findall(".//item"):
            title = _normalize_text(item.findtext("title"))
            link = _normalize_text(item.findtext("link"))
            excerpt = _normalize_text(item.findtext("description") or item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded"))
            published = _normalize_text(item.findtext("pubDate"))
            if not title or not link:
                continue
            entries.append(
                {
                    "title": title,
                    "url": urljoin(base_url, link),
                    "excerpt": excerpt[:400],
                    "published_at": published,
                    "source_id": source["source_id"],
                    "discovery_metadata": {"adapter_type": source.get("adapter_type")},
                }
            )
    else:
        for entry in root.findall(".//atom:entry", ns):
            title = _normalize_text(entry.findtext("atom:title", default="", namespaces=ns))
            excerpt = _normalize_text(
                entry.findtext("atom:summary", default="", namespaces=ns)
                or entry.findtext("atom:content", default="", namespaces=ns)
            )
            published = _normalize_text(
                entry.findtext("atom:published", default="", namespaces=ns)
                or entry.findtext("atom:updated", default="", namespaces=ns)
            )
            link = ""
            for candidate in entry.findall("atom:link", ns):
                href = _normalize_text(candidate.attrib.get("href"))
                rel = _normalize_text(candidate.attrib.get("rel") or "alternate")
                if href and rel in {"alternate", ""}:
                    link = href
                    break
            if not title or not link:
                continue
            entries.append(
                {
                    "title": title,
                    "url": urljoin(base_url, link),
                    "excerpt": excerpt[:400],
                    "published_at": published,
                    "source_id": source["source_id"],
                    "discovery_metadata": {"adapter_type": source.get("adapter_type")},
                }
            )
    return entries


def _extract_html_headlines(url: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    response = _request_get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    domain = urlparse(url).netloc
    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(url, str(anchor.get("href") or "").strip())
        parsed = urlparse(href)
        title = _normalize_text(anchor.get_text(" ", strip=True))
        if (
            not href
            or parsed.scheme not in {"http", "https"}
            or parsed.netloc != domain
            or len(title) < 25
            or href in seen_urls
        ):
            continue
        seen_urls.add(href)
        entries.append(
            {
                "title": title,
                "url": href,
                "excerpt": "",
                "published_at": None,
                "source_id": source["source_id"],
                "discovery_metadata": {"adapter_type": source.get("adapter_type")},
            }
        )
        if len(entries) >= 20:
            break
    return entries


def _extract_hn_top(source: dict[str, Any]) -> list[dict[str, Any]]:
    base = "https://hacker-news.firebaseio.com/v0"
    story_ids = _request_get(f"{base}/topstories.json").json()
    entries: list[dict[str, Any]] = []
    for item_id in list(story_ids or [])[:20]:
        item = _request_get(f"{base}/item/{item_id}.json").json() or {}
        title = _normalize_text(item.get("title"))
        url = _normalize_text(item.get("url") or f"https://news.ycombinator.com/item?id={item_id}")
        if not title or not url:
            continue
        entries.append(
            {
                "title": title,
                "url": url,
                "excerpt": "",
                "published_at": _isoformat(datetime.fromtimestamp(int(item.get("time") or 0), tz=timezone.utc)),
                "source_id": source["source_id"],
                "discovery_metadata": {
                    "adapter_type": source.get("adapter_type"),
                    "hn_id": item_id,
                    "score": int(item.get("score") or 0),
                    "descendants": int(item.get("descendants") or 0),
                    "text": _normalize_text(BeautifulSoup(item.get("text") or "", "html.parser").get_text(" ", strip=True)),
                },
            }
        )
    return entries


def _extract_arxiv_entries(source: dict[str, Any]) -> list[dict[str, Any]]:
    url = (
        "http://export.arxiv.org/api/query?"
        "search_query=cat:cs.AI+OR+cat:cs.LG&sortBy=submittedDate&sortOrder=descending&start=0&max_results=15"
    )
    response = _request_get(url)
    response.raise_for_status()
    return _parse_rss_or_atom(response.text, url, source)


def _discover_source_entries(source: dict[str, Any]) -> list[dict[str, Any]]:
    adapter_type = source.get("adapter_type")
    if adapter_type == "rss_atom":
        entries: list[dict[str, Any]] = []
        for seed_url in source.get("seed_urls", []):
            response = _request_get(seed_url)
            response.raise_for_status()
            entries.extend(_parse_rss_or_atom(response.text, seed_url, source))
        return entries
    if adapter_type == "html_headlines":
        entries = []
        for seed_url in source.get("seed_urls", []):
            try:
                entries.extend(_extract_html_headlines(seed_url, source))
            except Exception as exc:
                log.warning("News html discovery failed for %s: %s", seed_url, exc)
        return entries
    if adapter_type == "hn_top":
        return _extract_hn_top(source)
    if adapter_type == "arxiv_api":
        return _extract_arxiv_entries(source)
    return []


def _extract_article_text(url: str) -> str:
    response = _request_get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    body_texts = [
        _normalize_text(node.get_text(" ", strip=True))
        for node in soup.find_all(["article", "p"])
    ]
    long_blocks = [item for item in body_texts if len(item) >= 60]
    if long_blocks:
        return "\n\n".join(long_blocks[:80])
    return _normalize_text(soup.get_text(" ", strip=True))


def _fetch_article_body(seed: dict[str, Any]) -> tuple[str, str]:
    source_id = seed.get("source_id")
    metadata = dict(seed.get("discovery_metadata") or {})
    if source_id == "hacker_news" and metadata.get("text"):
        title = _normalize_text(seed.get("title"))
        body = _normalize_text(metadata.get("text"))
        if body:
            return title, body
    title = _normalize_text(seed.get("title"))
    body = _extract_article_text(seed["url"])
    return title, body


def _article_content_hash(title: str, body: str) -> str:
    return _sha256_text(f"{title}\n\n{body}")


def _article_id_for(seed: dict[str, Any], title: str, body: str) -> str:
    url_hash = _sha256_text(_normalize_text(seed.get("url")))[:12]
    content_hash = _article_content_hash(title, body)[:12]
    return f"{url_hash}-{content_hash}"


def discover_and_fetch_news(
    *,
    config_or_path: Any = None,
    limit_per_source: int = 12,
) -> dict[str, Any]:
    roots = resolve_news_roots(config_or_path)
    registry = load_news_source_registry(config_or_path)
    fetched_article_ids: list[str] = []
    seen_urls: set[str] = set()
    discovered_total = 0
    failed_sources: list[dict[str, str]] = []

    for source in registry:
        if not source.get("enabled"):
            continue
        try:
            entries = _discover_source_entries(source)[:limit_per_source]
        except Exception as exc:
            log.warning("News discovery failed for %s: %s", source.get("source_id"), exc)
            failed_sources.append(
                {"source_id": source.get("source_id", ""), "error": str(exc)}
            )
            continue

        discovered_total += len(entries)
        for seed in entries:
            url = _normalize_text(seed.get("url"))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                title, body = _fetch_article_body(seed)
            except Exception as exc:
                log.warning("News article fetch failed for %s: %s", url, exc)
                continue
            if len(_normalize_text(body)) < 120:
                continue

            article_id = _article_id_for(seed, title, body)
            article_dir = _article_dir(roots.article_store_root, article_id)
            article_dir.mkdir(parents=True, exist_ok=True)
            sentence_index = _sentence_index(body)
            published_at = _parse_datetime(seed.get("published_at"))
            article_payload = {
                "article_id": article_id,
                "source_id": seed.get("source_id"),
                "url": url,
                "title": title,
                "excerpt": _normalize_text(seed.get("excerpt"))[:400],
                "published_at": _isoformat(published_at) or _isoformat(_utc_now()),
                "fetched_at": _isoformat(_utc_now()),
                "content_hash": _article_content_hash(title, body),
                "raw_text_md": body,
                "sentence_index": sentence_index,
                "discovery_metadata": seed.get("discovery_metadata") or {},
            }
            _write_json(article_dir / "article.json", article_payload)
            fetched_article_ids.append(article_id)

    return {
        "status": "ok",
        "fetched_article_ids": fetched_article_ids,
        "fetched_count": len(fetched_article_ids),
        "discovered_total": discovered_total,
        "failed_sources": failed_sources,
    }


def prefetch_related_once(
    *,
    config_or_path: Any = None,
    article_ids: list[str] | None = None,
    max_related_per_seed: int = 3,
) -> dict[str, Any]:
    roots = resolve_news_roots(config_or_path)
    available_ids = article_ids or _list_article_ids(roots.article_store_root)
    articles = []
    for article_id in available_ids:
        try:
            articles.append(_load_article_record(roots.article_store_root, article_id))
        except Exception:
            continue

    related_map: dict[str, list[str]] = {}
    for article in articles:
        source_id = article.get("source_id")
        title_terms = set(_significant_terms(article.get("title")))
        if len(title_terms) < 2:
            continue
        candidates: list[tuple[int, str]] = []
        for other in articles:
            if other["article_id"] == article["article_id"] or other.get("source_id") != source_id:
                continue
            overlap = len(title_terms & set(_significant_terms(other.get("title"))))
            if overlap >= 2:
                candidates.append((overlap, other["article_id"]))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        related_map[article["article_id"]] = [item[1] for item in candidates[:max_related_per_seed]]

    prefetched_ids = sorted({item for values in related_map.values() for item in values})
    return {
        "status": "ok",
        "prefetched_article_ids": prefetched_ids,
        "related_map": related_map,
    }


def _significant_terms(text: Any) -> list[str]:
    lowered = re.findall(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_./+-]{2,}", str(text or "").lower())
    seen: set[str] = set()
    terms: list[str] = []
    for term in lowered:
        if term in NEWS_STOP_TERMS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _guess_claim_kind(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b\d[\d,.\-]*\b", lowered):
        return "count"
    if any(term in lowered for term in NEWS_CONFLICT_STATUS_TERMS):
        return "status"
    if any(term in lowered for term in {"approved", "decided", "voted", "signed"}):
        return "decision"
    if re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{4})\b", lowered):
        return "date_time"
    return "official_position"


def _extract_entities(text: str) -> list[str]:
    entities: set[str] = set()
    for match in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b", str(text or "")):
        cleaned = _normalize_text(match)
        if len(cleaned) >= 3:
            entities.add(cleaned)
    for match in re.findall(r"\b[А-Я][а-я]+(?:\s+[А-Я][а-я]+){0,2}\b", str(text or "")):
        cleaned = _normalize_text(match)
        if len(cleaned) >= 3:
            entities.add(cleaned)
    return sorted(entities)[:12]


def _domain_scores(article: dict[str, Any]) -> dict[str, float]:
    text = " ".join(
        [
            article.get("title", ""),
            article.get("excerpt", ""),
            article.get("raw_text_md", "")[:4000],
            " ".join(article.get("discovery_metadata", {}).get("topic_tags", []) or []),
        ]
    ).lower()
    scores: dict[str, float] = {}
    for domain, keywords in NEWS_DOMAIN_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in text)
        scores[domain] = min(1.0, hits / 4.0)
    if article.get("source_id") in {"kapital", "dnevnik"}:
        scores["bulgaria"] = min(1.0, scores.get("bulgaria", 0.0) + 0.4)
    if article.get("source_id") in {"politico_europe", "bbc_news"}:
        scores["europe"] = min(1.0, scores.get("europe", 0.0) + 0.2)
    return scores


def _freshness_score(article: dict[str, Any]) -> float:
    published_at = _parse_datetime(article.get("published_at")) or _utc_now()
    age_hours = max(0.0, (_utc_now() - published_at).total_seconds() / 3600.0)
    return max(0.0, min(1.0, 1.0 - (age_hours / 72.0)))


def _activity_score(article: dict[str, Any]) -> float:
    metadata = article.get("discovery_metadata", {}) or {}
    if article.get("source_id") == "hacker_news":
        score = float(metadata.get("score") or 0)
        comments = float(metadata.get("descendants") or 0)
        return min(1.0, (score / 500.0) + (comments / 300.0))
    return 0.0


def _score_sentence(sentence: dict[str, Any], title_terms: list[str]) -> float:
    text = str(sentence.get("text") or "")
    lowered = text.lower()
    keyword_hits = sum(1 for term in title_terms if term in lowered)
    length_bonus = min(1.0, len(text) / 180.0)
    return keyword_hits * 1.5 + length_bonus


def _build_evidence_spans(article: dict[str, Any]) -> list[dict[str, Any]]:
    title_terms = _significant_terms(article.get("title"))
    scored = sorted(
        article.get("sentence_index", []),
        key=lambda item: (-_score_sentence(item, title_terms), int(item.get("char_start", 0))),
    )
    retained = sorted(
        scored[:NEWS_MAX_EVIDENCE_SPANS_PER_ARTICLE],
        key=lambda item: int(item.get("char_start", 0)),
    )
    spans: list[dict[str, Any]] = []
    for index, sentence in enumerate(retained, start=1):
        text = _normalize_text(sentence.get("text"))
        if not text:
            continue
        entities = _extract_entities(text)
        spans.append(
            {
                "span_id": f"{article['article_id']}:span:{index}",
                "article_id": article["article_id"],
                "sentence_ids": [sentence["sentence_id"]],
                "char_start": int(sentence["char_start"]),
                "char_end": int(sentence["char_end"]),
                "verbatim_text": text,
                "attribution": "reported",
                "certainty": "reported",
                "time_scope": article.get("published_at"),
                "speaker_type": "reporter",
                "claim_kind_hint": _guess_claim_kind(text),
                "entity_keys": [_normalize_slug(item) for item in entities[:3]] or ["general"],
            }
        )
    return spans


def _build_claim_candidates(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for span in spans:
        text = span.get("verbatim_text", "")
        candidates.append(
            {
                "claim_candidate_id": f"{span['span_id']}:claim",
                "source_span_id": span["span_id"],
                "text": text,
                "claim_kind_hint": span.get("claim_kind_hint"),
                "entity_keys": list(span.get("entity_keys") or []),
            }
        )
    return candidates


def _build_story_hints(article: dict[str, Any], domain_scores: dict[str, float]) -> dict[str, Any]:
    top_domains = [
        domain for domain, score in sorted(domain_scores.items(), key=lambda item: (-item[1], item[0])) if score > 0
    ]
    return {
        "headline_terms": _significant_terms(article.get("title"))[:8],
        "entity_terms": [_normalize_slug(item) for item in _extract_entities(article.get("title", ""))[:6]],
        "top_domains": top_domains[:3],
    }


def _analyze_article_heuristic(article: dict[str, Any]) -> dict[str, Any]:
    spans = _build_evidence_spans(article)
    domain_scores = _domain_scores(article)
    text_window = f"{article.get('title', '')} {article.get('excerpt', '')} {article.get('raw_text_md', '')[:2500]}".lower()
    needs_context = any(term in text_window for term in NEWS_CONTEXT_TERMS)
    return {
        "article_id": article["article_id"],
        "evidence_spans": spans,
        "claim_candidates": _build_claim_candidates(spans),
        "story_hints": _build_story_hints(article, domain_scores),
        "domain_scores": domain_scores,
        "novelty_score": _freshness_score(article),
        "activity_score": _activity_score(article),
        "needs_context": needs_context,
    }


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    raw = _normalize_text(text)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _coerce_timeout_seconds(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(5, parsed)


def _news_model_timeout_seconds(kind: str, *, config_or_path: Any = None) -> int:
    if kind == "brief":
        return _coerce_timeout_seconds(
            _config_value(
                config_or_path,
                "NEWS_BRIEF_MODEL_TIMEOUT_SECONDS",
                DEFAULT_NEWS_BRIEF_MODEL_TIMEOUT_SECONDS,
            ),
            default=DEFAULT_NEWS_BRIEF_MODEL_TIMEOUT_SECONDS,
        )
    return _coerce_timeout_seconds(
        _config_value(
            config_or_path,
            "NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS",
            DEFAULT_NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS,
        ),
        default=DEFAULT_NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS,
    )


def _openai_compatible_chat_completion(
    *,
    endpoint: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: int,
) -> Optional[str]:
    if not endpoint or not model:
        return None

    base_url = endpoint.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    response = requests.post(
        f"{base_url}/chat/completions",
        timeout=(DEFAULT_NEWS_MODEL_CONNECT_TIMEOUT_SECONDS, timeout_seconds),
        headers={"Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
    )
    response.raise_for_status()
    payload = response.json() or {}
    choices = payload.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    return _normalize_text(message.get("content"))


def _merge_remote_analysis(
    article: dict[str, Any],
    base_analysis: dict[str, Any],
    *,
    config_or_path: Any = None,
) -> dict[str, Any]:
    endpoint = _normalize_text(_config_value(config_or_path, "NEWS_ARTICLE_MODEL_ENDPOINT", ""))
    model = _normalize_text(_config_value(config_or_path, "NEWS_ARTICLE_MODEL", ""))
    if not endpoint or not model:
        return base_analysis

    try:
        response_text = _openai_compatible_chat_completion(
            endpoint=endpoint,
            model=model,
            timeout_seconds=_news_model_timeout_seconds("article", config_or_path=config_or_path),
            system_prompt=(
                "You are an article analyst for a local news pipeline. Return compact JSON only. "
                "Do not invent facts not present in the article."
            ),
            user_prompt=(
                "Analyze this article and return JSON with keys "
                "`story_hints`, `domain_scores`, `claim_candidates`, `needs_context`, "
                "`novelty_score`, `activity_score`.\n\n"
                f"Title: {article.get('title', '')}\n"
                f"Source: {article.get('source_id', '')}\n"
                f"Text:\n{article.get('raw_text_md', '')[:12000]}"
            ),
        )
        remote = _extract_json_object(response_text or "")
        if not remote:
            return base_analysis
        merged = dict(base_analysis)
        if isinstance(remote.get("story_hints"), dict):
            merged["story_hints"] = {
                **merged.get("story_hints", {}),
                **remote["story_hints"],
            }
        if isinstance(remote.get("domain_scores"), dict):
            merged["domain_scores"] = {
                key: max(0.0, min(1.0, float(value)))
                for key, value in remote["domain_scores"].items()
                if isinstance(key, str)
            }
        if isinstance(remote.get("claim_candidates"), list):
            merged["claim_candidates"] = [
                item for item in remote["claim_candidates"][: NEWS_MAX_EVIDENCE_SPANS_PER_ARTICLE]
                if isinstance(item, dict)
            ] or merged["claim_candidates"]
        if isinstance(remote.get("needs_context"), bool):
            merged["needs_context"] = remote["needs_context"]
        if remote.get("novelty_score") is not None:
            merged["novelty_score"] = max(0.0, min(1.0, float(remote["novelty_score"])))
        if remote.get("activity_score") is not None:
            merged["activity_score"] = max(0.0, min(1.0, float(remote["activity_score"])))
        return merged
    except Exception as exc:
        log.warning("News remote article analysis failed for %s: %s", article.get("article_id"), exc)
        return base_analysis


def analyze_articles(
    *,
    config_or_path: Any = None,
    article_ids: list[str] | None = None,
) -> dict[str, Any]:
    roots = resolve_news_roots(config_or_path)
    target_ids = article_ids or _list_article_ids(roots.article_store_root)
    analyzed_ids: list[str] = []

    for article_id in target_ids:
        article = _load_article_record(roots.article_store_root, article_id)
        analysis = _merge_remote_analysis(
            article,
            _analyze_article_heuristic(article),
            config_or_path=config_or_path,
        )
        _write_json(_article_dir(roots.article_store_root, article_id) / "analysis.json", analysis)
        analyzed_ids.append(article_id)

    return {
        "status": "ok",
        "analyzed_article_ids": analyzed_ids,
        "analyzed_count": len(analyzed_ids),
        "prompt_version": NEWS_ANALYZER_PROMPT_VERSION,
    }


def _title_similarity(a: str, b: str) -> float:
    terms_a = set(_significant_terms(a))
    terms_b = set(_significant_terms(b))
    if not terms_a or not terms_b:
        return 0.0
    return len(terms_a & terms_b) / max(1, len(terms_a | terms_b))


def _article_representative_summary(article: dict[str, Any], analysis: dict[str, Any] | None) -> str:
    if analysis:
        spans = analysis.get("evidence_spans", []) or []
        if spans:
            return _normalize_text(spans[0].get("verbatim_text"))
    excerpt = _normalize_text(article.get("excerpt"))
    if excerpt:
        return excerpt
    first_sentence = article.get("sentence_index", [{}])[0]
    return _normalize_text(first_sentence.get("text"))


def _cluster_spans_for_story(spans: list[dict[str, Any]], article_lookup: dict[str, dict[str, Any]], story_candidate_id: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for span in spans:
        published_at = _parse_datetime(article_lookup[span["article_id"]].get("published_at")) or _utc_now()
        time_bucket = published_at.strftime("%Y-%m-%d")
        entity_key = (span.get("entity_keys") or ["general"])[0]
        key = (
            entity_key,
            time_bucket,
            str(span.get("claim_kind_hint") or "official_position"),
            str(span.get("certainty") or "reported"),
        )
        grouped.setdefault(key, []).append(span)

    clusters: list[dict[str, Any]] = []
    for index, (key, members) in enumerate(sorted(grouped.items()), start=1):
        representative = sorted(
            members,
            key=lambda item: (
                -len(str(item.get("verbatim_text") or "")),
                len(str(item.get("verbatim_text") or "")),
                article_lookup[item["article_id"]].get("published_at", ""),
                item["span_id"],
            ),
        )[0]
        clusters.append(
            {
                "cluster_id": f"{story_candidate_id}:cluster:{index}",
                "story_candidate_id": story_candidate_id,
                "representative_span_id": representative["span_id"],
                "span_ids": [item["span_id"] for item in members],
                "article_ids": sorted({item["article_id"] for item in members}),
                "source_count": len(
                    {
                        article_lookup[item["article_id"]].get("source_id")
                        for item in members
                    }
                ),
                "primary_entity_key": key[0],
                "time_bucket": key[1],
                "claim_kind_hint": key[2],
                "certainty": key[3],
                "preview": representative.get("verbatim_text"),
            }
        )
    return clusters


def _extract_numeric_value(text: str) -> Optional[float]:
    match = re.search(r"\b(\d[\d,]*(?:\.\d+)?)\b", str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except Exception:
        return None


def _detect_conflict_flags(spans: list[dict[str, Any]], article_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for span in spans:
        entity_key = (span.get("entity_keys") or ["general"])[0]
        published_at = _parse_datetime(article_lookup[span["article_id"]].get("published_at")) or _utc_now()
        time_bucket = published_at.strftime("%Y-%m-%d")
        key = (entity_key, time_bucket, str(span.get("claim_kind_hint") or "official_position"))
        grouped.setdefault(key, []).append(span)

    conflicts: list[dict[str, Any]] = []
    for key, members in grouped.items():
        if len(members) < 2:
            continue
        if key[2] == "count":
            values = {
                _extract_numeric_value(item.get("verbatim_text"))
                for item in members
                if _extract_numeric_value(item.get("verbatim_text")) is not None
            }
            if len(values) > 1:
                conflicts.append(
                    {
                        "entity_key": key[0],
                        "time_bucket": key[1],
                        "claim_kind": key[2],
                        "span_ids": [item["span_id"] for item in members],
                    }
                )
        elif key[2] == "status":
            statuses = {
                next(
                    (term for term in NEWS_CONFLICT_STATUS_TERMS if term in str(item.get("verbatim_text") or "").lower()),
                    None,
                )
                for item in members
            }
            statuses.discard(None)
            if len(statuses) > 1:
                conflicts.append(
                    {
                        "entity_key": key[0],
                        "time_bucket": key[1],
                        "claim_kind": key[2],
                        "span_ids": [item["span_id"] for item in members],
                    }
                )
    return conflicts


def _assign_story_categories(
    story_title: str,
    story_summary: str,
    category_config: list[dict[str, Any]],
    aggregate_domain_scores: dict[str, float],
) -> tuple[list[str], dict[str, float]]:
    text = f"{story_title} {story_summary}".lower()
    category_scores: dict[str, float] = {}
    for category in category_config:
        category_id = category["category_id"]
        score = float(aggregate_domain_scores.get(category_id, 0.0))
        score += min(
            0.6,
            sum(0.2 for term in category.get("assignment_terms", []) if term in text),
        )
        category_scores[category_id] = min(1.0, score)
    assigned = [
        category_id
        for category_id, score in sorted(category_scores.items(), key=lambda item: (-item[1], item[0]))
        if score > 0
    ][:2]
    return assigned, category_scores


def compile_story_candidates(
    *,
    config_or_path: Any = None,
    article_ids: list[str] | None = None,
    prefetched_article_ids: list[str] | None = None,
) -> dict[str, Any]:
    roots = resolve_news_roots(config_or_path)
    category_config = load_news_category_config(config_or_path)
    selected_ids = article_ids or _list_article_ids(roots.article_store_root)

    articles: list[dict[str, Any]] = []
    analyses_by_article: dict[str, dict[str, Any]] = {}
    for article_id in selected_ids:
        article = _load_article_record(roots.article_store_root, article_id)
        analysis = _load_analysis_record(roots.article_store_root, article_id)
        if analysis is None:
            continue
        articles.append(article)
        analyses_by_article[article_id] = analysis

    articles.sort(key=lambda item: (item.get("published_at", ""), item["article_id"]))
    used: set[str] = set()
    stories: list[dict[str, Any]] = []
    article_lookup = {item["article_id"]: item for item in articles}

    for article in articles:
        if article["article_id"] in used:
            continue
        story_members = [article]
        used.add(article["article_id"])
        for other in articles:
            if other["article_id"] in used:
                continue
            published_a = _parse_datetime(article.get("published_at")) or _utc_now()
            published_b = _parse_datetime(other.get("published_at")) or _utc_now()
            if abs((published_b - published_a).total_seconds()) > 72 * 3600:
                continue
            similarity = _title_similarity(article.get("title", ""), other.get("title", ""))
            shared_domains = set(
                analyses_by_article[article["article_id"]].get("story_hints", {}).get("top_domains", [])
            ) & set(analyses_by_article[other["article_id"]].get("story_hints", {}).get("top_domains", []))
            if similarity >= 0.35 or (similarity >= 0.2 and shared_domains):
                story_members.append(other)
                used.add(other["article_id"])

        primary = sorted(story_members, key=lambda item: (item.get("published_at", ""), item["article_id"]))[0]
        story_candidate_id = f"story:{primary['article_id']}"
        all_spans = []
        aggregate_domains: dict[str, float] = {}
        source_ids = sorted({item.get("source_id") for item in story_members if item.get("source_id")})
        for member in story_members:
            analysis = analyses_by_article[member["article_id"]]
            all_spans.extend(analysis.get("evidence_spans", []))
            for domain, score in (analysis.get("domain_scores", {}) or {}).items():
                aggregate_domains[domain] = max(float(score or 0.0), aggregate_domains.get(domain, 0.0))

        story_summary = _article_representative_summary(primary, analyses_by_article[primary["article_id"]])
        category_ids, category_scores = _assign_story_categories(
            primary.get("title", ""),
            story_summary,
            category_config,
            aggregate_domains,
        )
        clusters = _cluster_spans_for_story(all_spans, article_lookup, story_candidate_id)
        conflicts = _detect_conflict_flags(all_spans, article_lookup)
        stories.append(
            {
                "story_candidate_id": story_candidate_id,
                "primary_article_id": primary["article_id"],
                "supporting_article_ids": [item["article_id"] for item in story_members if item["article_id"] != primary["article_id"]],
                "thread_id": story_candidate_id,
                "category_ids": category_ids,
                "category_scores": category_scores,
                "source_ids": source_ids,
                "source_count": len(source_ids),
                "title": primary.get("title"),
                "summary": story_summary,
                "conflict_flags": conflicts,
                "evidence_clusters": clusters,
                "cluster_ids": [item["cluster_id"] for item in clusters],
                "freshness_score": max(_freshness_score(item) for item in story_members),
                "activity_score": max(_activity_score(item) for item in story_members),
                "relevance_score": max(category_scores.values() or [0.0]),
            }
        )

    return {
        "status": "ok",
        "story_candidates": stories,
        "article_count": len(articles),
        "prefetched_article_ids": sorted(set(prefetched_article_ids or [])),
    }


def build_snapshot(
    *,
    config_or_path: Any = None,
    article_ids: list[str] | None = None,
    prefetched_article_ids: list[str] | None = None,
) -> dict[str, Any]:
    roots = resolve_news_roots(config_or_path)
    story_bundle = compile_story_candidates(
        config_or_path=config_or_path,
        article_ids=article_ids,
        prefetched_article_ids=prefetched_article_ids,
    )
    registry = load_news_source_registry(config_or_path)
    categories = load_news_category_config(config_or_path)
    built_at = _utc_now()
    snapshot_id = built_at.strftime("%Y%m%dT%H%M%SZ")
    snapshot = {
        "snapshot_id": snapshot_id,
        "status": "closed",
        "built_at": _isoformat(built_at),
        "article_ids": sorted(article_ids or _list_article_ids(roots.article_store_root)),
        "prefetched_article_ids": sorted(set(prefetched_article_ids or [])),
        "analyzer_model_id": _normalize_text(_config_value(config_or_path, "NEWS_ARTICLE_MODEL", "")),
        "analyzer_prompt_version": NEWS_ANALYZER_PROMPT_VERSION,
        "compiler_version": NEWS_COMPILER_VERSION,
        "category_config_semantic_hash": news_category_config_semantic_hash(
            categories, source_registry=registry
        ),
        "source_registry_semantic_hash": news_source_registry_semantic_hash(registry),
        "resolved_category_config": categories,
        "resolved_source_registry": registry,
        "story_candidates": story_bundle["story_candidates"],
        "stats": {
            "story_candidate_count": len(story_bundle["story_candidates"]),
            "article_count": story_bundle["article_count"],
        },
    }
    snapshot_dir = roots.corpus_root / "snapshots" / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    _write_json(snapshot_dir / "snapshot.json", snapshot)
    _write_json(roots.corpus_root / "latest_snapshot.json", snapshot)
    return snapshot


def load_latest_closed_snapshot(config_or_path: Any = None) -> Optional[dict[str, Any]]:
    roots = resolve_news_roots(config_or_path)
    latest_path = roots.corpus_root / "latest_snapshot.json"
    if latest_path.exists():
        return _read_json(latest_path)

    snapshots_root = roots.corpus_root / "snapshots"
    if not snapshots_root.exists():
        return None
    candidates = sorted(snapshots_root.glob("*/snapshot.json"))
    if not candidates:
        return None
    return _read_json(candidates[-1])


def compute_story_candidate_score(
    story_candidate: dict[str, Any],
    *,
    category: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_presence = min(1.0, float(story_candidate.get("source_count", 0)) / 3.0)
    freshness = float(story_candidate.get("freshness_score") or 0.0)
    relevance = float(story_candidate.get("relevance_score") or 0.0)
    if category is not None:
        relevance = float(
            (story_candidate.get("category_scores", {}) or {}).get(category["category_id"], relevance)
        )
    activity = float(story_candidate.get("activity_score") or 0.0)
    base_score = min(
        1.0,
        max(
            0.0,
            (0.35 * source_presence)
            + (0.30 * freshness)
            + (0.25 * relevance)
            + (0.10 * activity),
        ),
    )
    preferred_bonus = 0.0
    preferred_bonus_applied = False
    if category is not None and set(category.get("preferred_source_ids", [])) & set(
        story_candidate.get("source_ids", [])
    ):
        preferred_bonus = NEWS_PREFERRED_SOURCE_BONUS_CAP
        preferred_bonus_applied = True
    final_score = max(0.0, min(1.0, base_score + preferred_bonus))
    return {
        "source_presence": source_presence,
        "freshness": freshness,
        "relevance": relevance,
        "activity": activity,
        "base_score": base_score,
        "preferred_bonus": min(preferred_bonus, NEWS_PREFERRED_SOURCE_BONUS_CAP),
        "preferred_bonus_applied": preferred_bonus_applied,
        "final_score": final_score,
    }


def select_stories_by_categories(
    snapshot: dict[str, Any],
    *,
    config_or_path: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    categories = load_news_category_config(config_or_path)
    stories = list(snapshot.get("story_candidates", []))
    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}
    enabled_categories = sorted(
        [item for item in categories if item.get("enabled")],
        key=lambda item: item["category_id"],
    )

    for category in enabled_categories:
        scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for story in stories:
            if story["story_candidate_id"] in selected_ids:
                continue
            if float((story.get("category_scores", {}) or {}).get(category["category_id"], 0.0)) <= 0:
                continue
            score_details = compute_story_candidate_score(story, category=category)
            if score_details["final_score"] <= 0:
                continue
            scored.append((score_details["final_score"], story, score_details))
        scored.sort(key=lambda item: (-item[0], item[1]["story_candidate_id"]))

        chosen: list[dict[str, Any]] = []
        preferred_bonus_count = 0
        non_preferred_count = 0
        capped_bonus_applications = 0
        for _, story, score_details in scored[: int(category.get("target_slots", 0) or 0)]:
            selected_ids.add(story["story_candidate_id"])
            selected_story = {
                **story,
                "selection_score": score_details["final_score"],
                "selection_score_details": score_details,
                "selected_category_id": category["category_id"],
            }
            selected.append(selected_story)
            chosen.append(selected_story)
            if score_details["preferred_bonus_applied"]:
                preferred_bonus_count += 1
                capped_bonus_applications += 1
            else:
                non_preferred_count += 1

        audit[category["category_id"]] = {
            "selected_story_count": len(chosen),
            "selected_stories_with_preferred_source_bonus": preferred_bonus_count,
            "selected_stories_without_preferred_source_bonus": non_preferred_count,
            "capped_bonus_applications": capped_bonus_applications,
        }

    total_slots = sum(int(item.get("target_slots", 0) or 0) for item in enabled_categories)
    fallback_slots = max(0, total_slots - len(selected))
    if fallback_slots > 0:
        fallback_candidates: list[tuple[float, dict[str, Any], dict[str, Any], str]] = []
        for story in stories:
            if story["story_candidate_id"] in selected_ids:
                continue
            best_category_id = ""
            best_score: dict[str, Any] | None = None
            for category in enabled_categories:
                if float((story.get("category_scores", {}) or {}).get(category["category_id"], 0.0)) <= 0:
                    continue
                score_details = compute_story_candidate_score(story, category=category)
                if best_score is None or score_details["final_score"] > best_score["final_score"]:
                    best_score = score_details
                    best_category_id = category["category_id"]
            if best_score is None:
                continue
            fallback_candidates.append(
                (best_score["final_score"], story, best_score, best_category_id)
            )
        fallback_candidates.sort(key=lambda item: (-item[0], item[1]["story_candidate_id"]))

        chosen = []
        for _, story, score_details, category_id in fallback_candidates[:fallback_slots]:
            selected_ids.add(story["story_candidate_id"])
            selected_story = {
                **story,
                "selection_score": score_details["final_score"],
                "selection_score_details": score_details,
                "selected_category_id": category_id,
                "selection_origin": "fallback",
            }
            selected.append(selected_story)
            chosen.append(selected_story)
        audit["__fallback__"] = {
            "selected_story_count": len(chosen),
            "selected_stories_with_preferred_source_bonus": sum(
                1 for item in chosen if item["selection_score_details"]["preferred_bonus_applied"]
            ),
            "selected_stories_without_preferred_source_bonus": sum(
                1 for item in chosen if not item["selection_score_details"]["preferred_bonus_applied"]
            ),
            "capped_bonus_applications": sum(
                1 for item in chosen if item["selection_score_details"]["preferred_bonus_applied"]
            ),
        }

    return selected, audit


def _bg_why_it_matters(story: dict[str, Any]) -> str:
    categories = list(story.get("category_ids") or [])
    if "tech_ai" in categories:
        return "Има значение, защото това мести терена за AI, compute или софтуерната инфраструктура."
    if "bulgaria" in categories:
        return "Има значение, защото това е локален сигнал, а не просто още един шумен цикъл."
    if "europe" in categories:
        return "Има значение, защото това почти винаги се превежда в правила, пари или търкания на континентално ниво."
    if "economy" in categories:
        return "Има значение, защото икономическите истории рядко питат удобно преди да стигнат до реалния свят."
    if "weird" in categories:
        return "Има значение, защото ако звучи странно, често е ранна индикация, че нещо се измества."
    return "Има значение, защото това променя политическия и оперативния контекст, не само заглавията."


def synthesize_briefing_script(selected_stories: list[dict[str, Any]]) -> str:
    if not selected_stories:
        return "Днес няма селекция, която да си струва да ти я продавам като брифинг."

    parts = ["Сутрешен брифинг."]
    for story in selected_stories:
        summary = _normalize_text(story.get("summary") or story.get("title"))
        if not summary:
            continue
        parts.append(summary.rstrip(".") + ".")
        parts.append(_bg_why_it_matters(story))
    return " ".join(parts)


def _synthesize_briefing_with_model(
    selected_stories: list[dict[str, Any]],
    *,
    config_or_path: Any = None,
) -> Optional[str]:
    endpoint = _normalize_text(_config_value(config_or_path, "NEWS_ARTICLE_MODEL_ENDPOINT", ""))
    model = _normalize_text(_config_value(config_or_path, "NEWS_BRIEF_MODEL", ""))
    if not endpoint or not model or not selected_stories:
        return None

    compact = [
        {
            "title": item.get("title"),
            "summary": item.get("summary"),
            "category_ids": item.get("category_ids", []),
            "source_ids": item.get("source_ids", []),
        }
        for item in selected_stories
    ]
    try:
        response_text = _openai_compatible_chat_completion(
            endpoint=endpoint,
            model=model,
            timeout_seconds=_news_model_timeout_seconds("brief", config_or_path=config_or_path),
            system_prompt=(
                "You write a Bulgarian morning news briefing. Keep it direct, concise, lightly ironic, "
                "and under 120 words. Return JSON only with key `script`."
            ),
            user_prompt=f"Write the briefing from these selected stories:\n{_canonical_json(compact)}",
        )
        payload = _extract_json_object(response_text or "")
        script = _normalize_text((payload or {}).get("script"))
        return script or None
    except Exception as exc:
        log.warning("News brief synthesis model failed: %s", exc)
        return None


def _write_silent_wav(path: Path, *, seconds: float) -> None:
    sample_rate = 16000
    frames = int(max(1.0, seconds) * sample_rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def render_briefing_tts(
    *,
    text: str,
    output_path: Path,
    voice_id: str = "",
) -> dict[str, Any]:
    if shutil.which("espeak-ng"):
        cmd = ["espeak-ng", "-w", str(output_path)]
        if voice_id:
            cmd.extend(["-v", voice_id])
        cmd.append(text)
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if completed.returncode == 0 and output_path.exists():
            return {"status": "ok", "engine": "espeak-ng", "path": str(output_path)}

    estimated_seconds = max(2.0, min(90.0, len(text.split()) / 2.4))
    _write_silent_wav(output_path, seconds=estimated_seconds)
    return {"status": "ok", "engine": "silent_fallback", "path": str(output_path)}


def build_briefing(
    *,
    config_or_path: Any = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    roots = resolve_news_roots(config_or_path)
    active_snapshot = snapshot or load_latest_closed_snapshot(config_or_path)
    if not active_snapshot:
        raise FileNotFoundError("No closed news snapshot available")

    selected_stories, category_audit = select_stories_by_categories(
        active_snapshot,
        config_or_path=config_or_path,
    )
    script = _synthesize_briefing_with_model(
        selected_stories, config_or_path=config_or_path
    ) or synthesize_briefing_script(selected_stories)
    today = (_parse_datetime(active_snapshot.get("built_at")) or _utc_now()).date().isoformat()
    briefing_dir = roots.briefings_root / today
    briefing_dir.mkdir(parents=True, exist_ok=True)
    audio_path = briefing_dir / f"briefing_{today}.wav"
    tts_result = render_briefing_tts(
        text=script,
        output_path=audio_path,
        voice_id=_normalize_text(_config_value(config_or_path, "NEWS_TTS_VOICE_ID", "")),
    )
    payload = {
        "snapshot_id": active_snapshot["snapshot_id"],
        "date": today,
        "selected_stories": selected_stories,
        "category_decisions": category_audit,
        "script": script,
        "source_refs": [
            {
                "story_candidate_id": story["story_candidate_id"],
                "article_ids": [story["primary_article_id"], *story.get("supporting_article_ids", [])],
                "source_ids": story.get("source_ids", []),
            }
            for story in selected_stories
        ],
        "audio_path": str(audio_path),
        "tts": tts_result,
    }
    _write_json(briefing_dir / "briefing.json", payload)
    _write_json(roots.briefings_root / "latest_briefing.json", payload)
    return payload


def _latest_briefing_payload(config_or_path: Any = None) -> Optional[dict[str, Any]]:
    roots = resolve_news_roots(config_or_path)
    latest = roots.briefings_root / "latest_briefing.json"
    if latest.exists():
        return _read_json(latest)
    candidates = sorted(roots.briefings_root.glob("*/briefing.json"))
    if not candidates:
        return None
    return _read_json(candidates[-1])


def play_latest_briefing(
    *,
    config_or_path: Any = None,
    volume: float = 0.55,
) -> dict[str, Any]:
    briefing = _latest_briefing_payload(config_or_path)
    if not briefing:
        raise FileNotFoundError("No news briefing available for playback")

    audio_path = Path(str(briefing.get("audio_path") or ""))
    if not audio_path.exists():
        raise FileNotFoundError(f"Missing audio artifact: {audio_path}")

    playback_device = _normalize_text(_config_value(config_or_path, "NEWS_PLAYBACK_DEVICE", ""))
    steps: list[dict[str, Any]] = []

    if playback_device and shutil.which("bluetoothctl"):
        completed = subprocess.run(
            ["bluetoothctl", "connect", playback_device],
            check=False,
            capture_output=True,
            text=True,
        )
        steps.append(
            {
                "step": "bluetooth_connect",
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )

    if shutil.which("wpctl"):
        completed = subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(volume)],
            check=False,
            capture_output=True,
            text=True,
        )
        steps.append(
            {
                "step": "set_volume",
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )

    if not shutil.which("pw-play"):
        raise FileNotFoundError("pw-play is not installed")

    completed = subprocess.run(
        ["pw-play", str(audio_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    steps.append(
        {
            "step": "play_audio",
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "pw-play failed")

    return {"status": "ok", "audio_path": str(audio_path), "steps": steps}


def _story_documents(snapshot: dict[str, Any], config_or_path: Any = None) -> list[dict[str, Any]]:
    roots = resolve_news_roots(config_or_path)
    documents: list[dict[str, Any]] = []
    for story in snapshot.get("story_candidates", []):
        article_ids = [story["primary_article_id"], *story.get("supporting_article_ids", [])]
        summary_lines = [story.get("summary", ""), f"Sources: {', '.join(story.get('source_ids', []))}"]
        documents.append(
            {
                "id": story["story_candidate_id"],
                "name": story.get("title") or story["story_candidate_id"],
                "type": "news_story_candidate",
                "content": "\n".join(line for line in summary_lines if line),
                "source_path": f"snapshot:{snapshot.get('snapshot_id')}:{story['story_candidate_id']}",
                "article_ids": article_ids,
                "category_ids": list(story.get("category_ids") or []),
            }
        )
    return documents


def consult_news_corpus(
    *,
    objective: str,
    phase: str = "start",
    current_findings: str = "",
    current_hypothesis: str = "",
    named_entity: str = "",
    config_or_path: Any = None,
) -> dict[str, Any]:
    snapshot = load_latest_closed_snapshot(config_or_path)
    if not snapshot:
        return {"status": "empty", "reason": "no_closed_snapshot"}

    query_terms = _significant_terms(" ".join([objective, current_findings, current_hypothesis, named_entity]))
    matches: list[tuple[float, dict[str, Any]]] = []
    for story in snapshot.get("story_candidates", []):
        haystack = " ".join(
            [
                story.get("title", ""),
                story.get("summary", ""),
                " ".join(story.get("category_ids", [])),
                " ".join(story.get("source_ids", [])),
            ]
        ).lower()
        score = sum(1 for term in query_terms if term in haystack)
        if named_entity and named_entity.lower() in haystack:
            score += 2
        if score > 0:
            matches.append((float(score), story))
    matches.sort(key=lambda item: (-item[0], item[1]["story_candidate_id"]))
    matched_stories = [story for _, story in matches[:6]]
    return {
        "status": "ok",
        "phase": phase,
        "objective": _normalize_text(objective),
        "snapshot_id": snapshot.get("snapshot_id"),
        "matched_stories": matched_stories,
        "source_documents": [item for item in _story_documents(snapshot, config_or_path=config_or_path) if item["id"] in {story["story_candidate_id"] for story in matched_stories}],
    }


def retrieve_news_articles(
    *,
    query: str,
    article_ids: list[str] | None = None,
    top_k: int = 8,
    config_or_path: Any = None,
) -> dict[str, Any]:
    snapshot = load_latest_closed_snapshot(config_or_path)
    if not snapshot:
        return {"status": "empty", "reason": "no_closed_snapshot"}

    query_terms = _significant_terms(query)
    items: list[tuple[float, dict[str, Any]]] = []
    for story in snapshot.get("story_candidates", []):
        if article_ids and story["primary_article_id"] not in set(article_ids) and not set(article_ids) & set(story.get("supporting_article_ids", [])):
            continue
        for cluster in story.get("evidence_clusters", []):
            haystack = f"{story.get('title', '')} {cluster.get('preview', '')}".lower()
            score = sum(1 for term in query_terms if term in haystack)
            if score <= 0:
                continue
            items.append(
                (
                    float(score),
                    {
                        "cluster_id": cluster["cluster_id"],
                        "story_candidate_id": story["story_candidate_id"],
                        "title": story.get("title"),
                        "preview": cluster.get("preview"),
                        "article_ids": cluster.get("article_ids", []),
                        "source_ids": story.get("source_ids", []),
                        "category_ids": story.get("category_ids", []),
                        "source_count": cluster.get("source_count", 0),
                    },
                )
            )
    items.sort(key=lambda item: (-item[0], item[1]["cluster_id"]))
    return {
        "status": "ok",
        "snapshot_id": snapshot.get("snapshot_id"),
        "query": _normalize_text(query),
        "items": [item for _, item in items[: max(1, min(int(top_k or 8), 20))]],
    }


def retrieve_news_timeline(
    *,
    query: str | None = None,
    thread_ids: list[str] | None = None,
    top_k: int = 8,
    config_or_path: Any = None,
) -> dict[str, Any]:
    snapshot = load_latest_closed_snapshot(config_or_path)
    if not snapshot:
        return {"status": "empty", "reason": "no_closed_snapshot"}

    query_terms = _significant_terms(query or "")
    items: list[tuple[float, dict[str, Any]]] = []
    for story in snapshot.get("story_candidates", []):
        if thread_ids and story.get("thread_id") not in set(thread_ids):
            continue
        haystack = f"{story.get('title', '')} {story.get('summary', '')}".lower()
        score = 1.0 if not query_terms else float(sum(1 for term in query_terms if term in haystack))
        if score <= 0:
            continue
        items.append(
            (
                score,
                {
                    "thread_id": story.get("thread_id"),
                    "story_candidate_id": story["story_candidate_id"],
                    "title": story.get("title"),
                    "summary": story.get("summary"),
                    "article_ids": [story["primary_article_id"], *story.get("supporting_article_ids", [])],
                    "source_ids": story.get("source_ids", []),
                    "category_ids": story.get("category_ids", []),
                },
            )
        )
    items.sort(key=lambda item: (-item[0], item[1]["story_candidate_id"]))
    return {
        "status": "ok",
        "snapshot_id": snapshot.get("snapshot_id"),
        "items": [item for _, item in items[: max(1, min(int(top_k or 8), 20))]],
        "source_documents": [
            {
                "id": item["story_candidate_id"],
                "name": item.get("title") or item["story_candidate_id"],
                "type": "news_timeline_entry",
                "content": _normalize_text(item.get("summary")),
                "source_path": f"timeline:{snapshot.get('snapshot_id')}:{item['story_candidate_id']}",
                "category_ids": item.get("category_ids", []),
            }
            for item in [item for _, item in items[: max(1, min(int(top_k or 8), 20))]]
        ],
    }


def view_news_articles(
    *,
    article_ids: list[str],
    config_or_path: Any = None,
) -> dict[str, Any]:
    roots = resolve_news_roots(config_or_path)
    items = []
    for article_id in article_ids:
        article = _load_article_record(roots.article_store_root, article_id)
        analysis = _load_analysis_record(roots.article_store_root, article_id)
        items.append(
            {
                "article_id": article["article_id"],
                "source_id": article.get("source_id"),
                "title": article.get("title"),
                "url": article.get("url"),
                "published_at": article.get("published_at"),
                "raw_text_md": article.get("raw_text_md"),
                "analysis": analysis,
            }
        )
    return {"status": "ok", "items": items}
