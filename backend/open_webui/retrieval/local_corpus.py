import csv
import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from open_webui.env import BASE_DIR, DATA_DIR

log = logging.getLogger(__name__)

LOCAL_CORPUS_SCHEMA_VERSION = 2
LOCAL_CORPUS_INDEX_DIR = DATA_DIR / "local_corpus"
DEFAULT_LOCAL_CORPUS_ROOT = BASE_DIR / "literature_corpus"
DEFAULT_LOCAL_CORPUS_ROOT_SETTING = Path("literature_corpus")

TABLE_LIKE_TERMS = {
    "criteria",
    "differential",
    "dose",
    "dosing",
    "grading",
    "grade",
    "lab",
    "labs",
    "range",
    "ranges",
    "score",
    "scores",
    "staging",
    "stage",
    "threshold",
    "thresholds",
}
ANTIMICROBIAL_TERMS = {
    "antibiotic",
    "antibiotics",
    "antimicrobial",
    "antimicrobials",
    "empiric",
    "empirical",
    "firstline",
    "first_line",
    "first-line",
    "regimen",
    "regimens",
}
ANTIMICROBIAL_BOOK_HINTS = {
    "adult",
    "adults",
    "antibiotic",
    "antimicrobial",
    "dose",
    "dosing",
    "empiric",
    "empirical",
    "guide",
    "guideline",
    "handbook",
    "infectious",
    "regimen",
    "resistance",
    "syndrome",
    "therapy",
    "treatment",
}
REGIMEN_EVIDENCE_TERMS = {
    "alternative",
    "alternatives",
    "empiric",
    "empirical",
    "firstline",
    "first_line",
    "first-line",
    "option",
    "options",
    "primary",
    "regimen",
    "regimens",
    "treat",
    "treatment",
}
TIME_SENSITIVE_TERMS = {
    "current",
    "latest",
    "new",
    "newest",
    "recent",
    "today",
    "updated",
}
MEDICAL_MANAGEMENT_TERMS = {
    "algorithm",
    "criteria",
    "diagnosis",
    "differential",
    "dose",
    "dosing",
    "management",
    "monitoring",
    "prevention",
    "protocol",
    "recommendation",
    "recommendations",
    "rule",
    "rules",
    "staging",
    "steps",
    "treatment",
    "workup",
}
MEDICAL_BACKGROUND_TERMS = {
    "background",
    "explain",
    "mechanism",
    "mechanisms",
    "pathogenesis",
    "pathophysiology",
    "physiology",
    "why",
}
SALIENT_QUERY_STOP_TERMS = (
    MEDICAL_MANAGEMENT_TERMS
    | MEDICAL_BACKGROUND_TERMS
    | ANTIMICROBIAL_TERMS
    | {
        "adult",
        "adults",
        "algorithm",
        "and",
        "for",
        "in",
        "initial",
        "of",
        "patient",
        "patients",
        "question",
        "the",
        "use",
        "with",
    }
)

_REGISTRY_CACHE: dict[tuple[str, float], "LocalCorpusRegistry"] = {}
_REGISTRY_LOCK = threading.Lock()
_INDEX_LOCKS: dict[str, threading.Lock] = {}


def clear_local_corpus_caches() -> None:
    with _REGISTRY_LOCK:
        _REGISTRY_CACHE.clear()
        _INDEX_LOCKS.clear()


@dataclass(frozen=True)
class LocalCorpusBook:
    domain: str
    primary_discipline: str
    book_id: str
    title: str
    resource_type: str
    evidence_tier: str
    authority_or_publisher: Optional[str]
    year: Optional[int]
    document_dir: Optional[str]
    selected_dir: Optional[str]
    parse_status: str
    quarantine_reason: Optional[str]
    secondary_tags: tuple[str, ...]
    coverage_phrases: tuple[str, ...]
    negative_scope: tuple[str, ...]
    clean_toc: tuple[str, ...]
    what_this_is: str
    table_count: int
    figure_count: int
    review_flags: tuple[str, ...]
    selected_dir_path: Optional[Path]
    retrieval_path: Optional[Path]
    card_path: Optional[Path]

    @property
    def usable(self) -> bool:
        return (
            self.parse_status == "success"
            and self.quarantine_reason in {None, ""}
            and self.selected_dir_path is not None
            and self.retrieval_path is not None
            and self.retrieval_path.exists()
        )


@dataclass
class LocalCorpusRegistry:
    root: Path
    catalog_path: Path
    domains_index_path: Path
    books_by_id: dict[str, LocalCorpusBook]
    usable_books_by_domain: dict[str, list[LocalCorpusBook]]
    domain_summaries: dict[str, str]
    discipline_summaries: dict[str, dict[str, str]]


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _portable_repo_root_fallback(
    candidate: Path,
    repo_relative_default: Path,
) -> Optional[Path]:
    if not candidate.is_absolute():
        return None

    expected_suffix = repo_relative_default.parts
    if len(candidate.parts) < len(expected_suffix):
        return None
    if candidate.parts[-len(expected_suffix) :] != expected_suffix:
        return None

    portable = (BASE_DIR / repo_relative_default).resolve()
    if portable.exists():
        log.warning(
            "Falling back from missing absolute corpus root %s to repo-relative %s",
            candidate,
            portable,
        )
        return portable
    return None


def resolve_repo_relative_corpus_root(
    candidate: Path,
    repo_relative_default: Path,
) -> Optional[Path]:
    expanded = candidate.expanduser()
    if not expanded.is_absolute():
        expanded = BASE_DIR / expanded

    resolved = expanded.resolve()
    if resolved.exists():
        return resolved

    return _portable_repo_root_fallback(expanded, repo_relative_default)


def _phrase_ready_text(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()),
    ).strip()


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_./+-]{1,}", query.lower())]


def _quoted_phrases(query: str) -> list[str]:
    return [_normalize_text(match).lower() for match in re.findall(r'"([^"]+)"', query or "")]


def _expanded_query_terms(query: str, domain: str = "") -> list[str]:
    terms = _query_terms(query)
    lowered = set(terms)
    expanded = list(terms)

    if lowered & ANTIMICROBIAL_TERMS:
        expanded.extend(
            [
                "antibiotic",
                "antimicrobial",
                "empiric",
                "empirical",
                "regimen",
                "therapy",
                "treatment",
            ]
        )

    if "pneumonia" in lowered and ({"community", "cap"} & lowered):
        expanded.extend(["community", "acquired", "pneumonia", "cap"])
    if "pneumonia" in lowered and ({"hospital", "hap", "nosocomial"} & lowered):
        expanded.extend(["hospital", "acquired", "pneumonia", "hap", "nosocomial"])
    if "pneumonia" in lowered and ({"ventilator", "vap"} & lowered):
        expanded.extend(["ventilator", "associated", "pneumonia", "vap"])

    if domain == "medicine" and lowered & {"adult", "adults"}:
        expanded.extend(["adult", "adults"])

    deduped: list[str] = []
    seen: set[str] = set()
    for term in expanded:
        normalized = term.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _salient_query_phrases(query: str) -> list[str]:
    words = [word for word in re.findall(r"[A-Za-z0-9]+", query.lower()) if word]
    filtered = [word for word in words if word not in SALIENT_QUERY_STOP_TERMS]
    phrases: list[str] = []
    for size in range(4, 1, -1):
        for index in range(0, max(0, len(filtered) - size + 1)):
            phrase = " ".join(filtered[index : index + size]).strip()
            if len(phrase) < 8:
                continue
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases[:8]


def _has_antimicrobial_intent(query: str) -> bool:
    return bool(set(_expanded_query_terms(query, "medicine")) & ANTIMICROBIAL_TERMS)


def _specific_query_terms(query: str, domain: str = "") -> list[str]:
    terms = _expanded_query_terms(query, domain)
    specific = [
        term
        for term in terms
        if term not in SALIENT_QUERY_STOP_TERMS and len(term) >= 4
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for term in specific:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped[:10]


def _specificity_signal(text: str, specific_terms: list[str]) -> tuple[int, float]:
    normalized = _phrase_ready_text(text)
    if not normalized or not specific_terms:
        return 0, 0.0
    hits = sum(1 for term in specific_terms if term in normalized)
    return hits, hits / max(1, len(specific_terms))


def _token_overlap_score(text: str, terms: list[str]) -> float:
    lowered = text.lower()
    if not lowered or not terms:
        return 0.0
    hits = sum(1 for term in terms if term in lowered)
    return hits / max(1, len(terms))


def _contains_any(terms: set[str], values: list[str]) -> bool:
    return any(value in terms for value in values)


def _parse_bullet_summaries(path: Path) -> dict[str, str]:
    summaries: dict[str, str] = {}
    if not path.exists():
        return summaries

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:]
        if ":" not in body:
            continue
        label, summary = body.split(":", 1)
        summaries[_normalize_name(label)] = _normalize_text(summary)
    return summaries


def resolve_local_corpus_root(config_or_path: Any = None) -> Optional[Path]:
    if config_or_path is None:
        candidate = DEFAULT_LOCAL_CORPUS_ROOT
    elif isinstance(config_or_path, (str, Path)):
        candidate = Path(config_or_path)
    else:
        raw = getattr(config_or_path, "LOCAL_CORPUS_ROOT", None)
        candidate = Path(str(raw)) if raw else DEFAULT_LOCAL_CORPUS_ROOT_SETTING

    return resolve_repo_relative_corpus_root(
        candidate,
        DEFAULT_LOCAL_CORPUS_ROOT_SETTING,
    )


def _registry_cache_key(root: Path, catalog_path: Path) -> tuple[str, float]:
    try:
        mtime = catalog_path.stat().st_mtime
    except Exception:
        mtime = 0.0
    return (str(root), mtime)


def load_local_corpus_registry(config_or_path: Any = None) -> LocalCorpusRegistry:
    root = resolve_local_corpus_root(config_or_path)
    if root is None:
        raise FileNotFoundError("Local corpus root could not be resolved")

    catalog_path = root / "_serving" / "serving-catalog.jsonl"
    if not catalog_path.exists():
        raise FileNotFoundError(f"Missing serving catalog: {catalog_path}")

    cache_key = _registry_cache_key(root, catalog_path)
    with _REGISTRY_LOCK:
        cached = _REGISTRY_CACHE.get(cache_key)
        if cached is not None:
            return cached

        books_by_id: dict[str, LocalCorpusBook] = {}
        usable_books_by_domain: dict[str, list[LocalCorpusBook]] = {}

        for line in catalog_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            book_id = str(row.get("book_id") or "").strip()
            if not book_id:
                continue

            domain = _normalize_name(row.get("domain") or "general")
            primary_discipline = _normalize_name(
                row.get("primary_discipline") or "general_reference"
            )
            selected_dir = row.get("selected_dir")
            selected_dir_path = root / selected_dir if selected_dir else None
            retrieval_path = (
                selected_dir_path / "retrieval.md" if selected_dir_path else None
            )
            card_path = (
                root
                / "_serving"
                / "domains"
                / domain
                / "books"
                / f"{book_id}.md"
            )
            book = LocalCorpusBook(
                domain=domain,
                primary_discipline=primary_discipline,
                book_id=book_id,
                title=_normalize_text(row.get("title")),
                resource_type=_normalize_name(row.get("resource_type") or "reference"),
                evidence_tier=_normalize_name(row.get("evidence_tier") or "reference"),
                authority_or_publisher=_normalize_text(row.get("authority_or_publisher")) or None,
                year=int(row["year"]) if isinstance(row.get("year"), int) else None,
                document_dir=_normalize_text(row.get("document_dir")) or None,
                selected_dir=_normalize_text(selected_dir) or None,
                parse_status=_normalize_name(row.get("parse_status") or "unknown"),
                quarantine_reason=_normalize_text(row.get("quarantine_reason")) or None,
                secondary_tags=tuple(_normalize_text(tag) for tag in row.get("secondary_tags") or []),
                coverage_phrases=tuple(
                    _normalize_text(tag) for tag in row.get("coverage_phrases") or []
                ),
                negative_scope=tuple(
                    _normalize_text(tag) for tag in row.get("negative_scope") or []
                ),
                clean_toc=tuple(_normalize_text(tag) for tag in row.get("clean_toc") or []),
                what_this_is=_normalize_text(row.get("what_this_is")),
                table_count=int(row.get("table_count") or 0),
                figure_count=int(row.get("figure_count") or 0),
                review_flags=tuple(
                    _normalize_text(tag) for tag in row.get("review_flags") or []
                ),
                selected_dir_path=selected_dir_path if selected_dir_path and selected_dir_path.exists() else None,
                retrieval_path=retrieval_path if retrieval_path and retrieval_path.exists() else None,
                card_path=card_path if card_path.exists() else None,
            )
            books_by_id[book_id] = book
            if book.usable:
                usable_books_by_domain.setdefault(domain, []).append(book)

        domain_summaries = _parse_bullet_summaries(root / "_serving" / "domains" / "index.md")
        discipline_summaries: dict[str, dict[str, str]] = {}
        for domain in usable_books_by_domain:
            discipline_summaries[domain] = _parse_bullet_summaries(
                root / "_serving" / "domains" / domain / "index.md"
            )

        registry = LocalCorpusRegistry(
            root=root,
            catalog_path=catalog_path,
            domains_index_path=root / "_serving" / "domains" / "index.md",
            books_by_id=books_by_id,
            usable_books_by_domain=usable_books_by_domain,
            domain_summaries=domain_summaries,
            discipline_summaries=discipline_summaries,
        )
        _REGISTRY_CACHE.clear()
        _REGISTRY_CACHE[cache_key] = registry
        return registry


def list_local_corpus_domains(config_or_path: Any = None) -> dict[str, Any]:
    registry = load_local_corpus_registry(config_or_path)
    items = []
    for domain, books in sorted(
        registry.usable_books_by_domain.items(),
        key=lambda item: (-len(item[1]), item[0]),
    ):
        discipline_count = len({book.primary_discipline for book in books})
        items.append(
            {
                "domain": domain,
                "label": domain.replace("_", " "),
                "book_count": len(books),
                "discipline_count": discipline_count,
                "summary": registry.domain_summaries.get(domain, ""),
            }
        )
    return {
        "status": "ok",
        "domains": items,
        "domain_count": len(items),
        "corpus_root": str(registry.root),
    }


def list_local_corpus_disciplines(
    domain: str,
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_local_corpus_registry(config_or_path)
    normalized_domain = _normalize_name(domain)
    books = registry.usable_books_by_domain.get(normalized_domain)
    if not books:
        return {
            "status": "error",
            "error": f"Unknown or unavailable domain: {domain}",
            "domain": normalized_domain,
            "disciplines": [],
        }

    discipline_counts: dict[str, int] = {}
    for book in books:
        discipline_counts[book.primary_discipline] = (
            discipline_counts.get(book.primary_discipline, 0) + 1
        )

    summaries = registry.discipline_summaries.get(normalized_domain, {})
    items = [
        {
            "domain": normalized_domain,
            "discipline": discipline,
            "label": discipline.replace("_", " "),
            "book_count": count,
            "summary": summaries.get(discipline, ""),
        }
        for discipline, count in sorted(
            discipline_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "status": "ok",
        "domain": normalized_domain,
        "disciplines": items,
        "discipline_count": len(items),
    }


def _route_domain(
    query: str,
    registry: LocalCorpusRegistry,
) -> dict[str, Any]:
    available_domains = sorted(registry.usable_books_by_domain.keys())
    if not available_domains:
        return {
            "selected_domain": None,
            "confidence": 0.0,
            "ambiguous": True,
            "ranked_domains": [],
        }
    if len(available_domains) == 1:
        return {
            "selected_domain": available_domains[0],
            "confidence": 1.0,
            "ambiguous": False,
            "ranked_domains": [
                {"domain": available_domains[0], "score": 1.0},
            ],
        }

    query_terms = _query_terms(query)
    ranked: list[dict[str, Any]] = []
    for domain in available_domains:
        books = registry.usable_books_by_domain.get(domain, [])
        disciplines = {book.primary_discipline for book in books}
        titles = [book.title for book in books[:25]]
        signal_text = " ".join(
            [
                domain.replace("_", " "),
                registry.domain_summaries.get(domain, ""),
                " ".join(discipline.replace("_", " ") for discipline in disciplines),
                " ".join(titles),
            ]
        )
        score = _token_overlap_score(signal_text, query_terms)
        if domain in query.lower():
            score += 0.8
        ranked.append({"domain": domain, "score": round(score, 4)})

    ranked.sort(key=lambda item: (-item["score"], item["domain"]))
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else {"score": 0.0}
    confidence = float(best["score"])
    ambiguous = confidence <= 0.55 or (confidence - float(second["score"])) < 0.2
    return {
        "selected_domain": None if ambiguous else best["domain"],
        "confidence": round(confidence, 4),
        "ambiguous": ambiguous,
        "ranked_domains": ranked,
    }


def _medical_query_mode(query: str) -> str:
    terms = set(_expanded_query_terms(query, "medicine"))
    if _contains_any(terms, list(MEDICAL_MANAGEMENT_TERMS)):
        return "management"
    if _contains_any(terms, list(MEDICAL_BACKGROUND_TERMS)):
        return "background"
    return "general"


def _book_score(book: LocalCorpusBook, query: str, domain: str) -> tuple[float, list[str]]:
    query_terms = _expanded_query_terms(query, domain)
    phrases = _quoted_phrases(query)
    salient_phrases = _salient_query_phrases(query)
    specific_terms = _specific_query_terms(query, domain)

    title_text = book.title.lower()
    coverage_text = " ".join(book.coverage_phrases).lower()
    toc_text = " ".join(book.clean_toc).lower()
    summary_text = f"{book.what_this_is} {' '.join(book.secondary_tags)}".lower()
    discipline_text = book.primary_discipline.replace("_", " ")
    phrase_title = _phrase_ready_text(book.title)
    phrase_coverage = _phrase_ready_text(" ".join(book.coverage_phrases))
    phrase_toc = _phrase_ready_text(" ".join(book.clean_toc))
    phrase_summary = _phrase_ready_text(
        f"{book.what_this_is} {' '.join(book.secondary_tags)} {' '.join(book.negative_scope)}"
    )
    combined_phrase_text = " ".join(
        [phrase_title, phrase_coverage, phrase_toc, phrase_summary, _phrase_ready_text(discipline_text)]
    )

    score = 0.0
    reasons: list[str] = []
    score += _token_overlap_score(title_text, query_terms) * 3.5
    score += _token_overlap_score(coverage_text, query_terms) * 4.5
    score += _token_overlap_score(toc_text, query_terms) * 1.8
    score += _token_overlap_score(summary_text, query_terms) * 1.4
    score += _token_overlap_score(discipline_text, query_terms) * 1.0

    lowered_query = _normalize_text(query).lower()
    if lowered_query and lowered_query in title_text:
        score += 2.5
        reasons.append("title")

    for phrase in phrases:
        if phrase and phrase in title_text:
            score += 1.5
            reasons.append("quoted_title")
        elif phrase and phrase in coverage_text:
            score += 1.2
            reasons.append("quoted_coverage")

    for phrase in salient_phrases:
        normalized_phrase = _phrase_ready_text(phrase)
        if normalized_phrase and normalized_phrase in phrase_title:
            score += 3.0
            reasons.append("salient_title")
        elif normalized_phrase and normalized_phrase in phrase_coverage:
            score += 2.4
            reasons.append("salient_coverage")
        elif normalized_phrase and normalized_phrase in phrase_toc:
            score += 1.4
            reasons.append("salient_contents")

    specific_hits, specific_ratio = _specificity_signal(combined_phrase_text, specific_terms)
    if specific_hits:
        score += specific_ratio * 2.6
        reasons.append("specificity")
    elif len(specific_terms) >= 2:
        score -= 0.8

    if domain == "medicine":
        mode = _medical_query_mode(query)
        if mode == "management":
            if book.resource_type in {"guideline", "manual"}:
                score += 2.4
                reasons.append("management_resource_type")
            elif book.resource_type in {"reference", "classification_reference", "handbook"}:
                score += 1.2
            elif book.resource_type == "textbook":
                score -= 0.4
        elif mode == "background":
            if book.resource_type == "textbook":
                score += 2.0
                reasons.append("background_textbook")
            elif book.resource_type == "reference":
                score += 0.8
            elif book.resource_type == "guideline":
                score -= 0.2

        if _contains_any(set(query_terms), list(TABLE_LIKE_TERMS)) and book.table_count > 0:
            score += 0.4
            reasons.append("table_dense")

        if _has_antimicrobial_intent(query):
            hint_overlap = _token_overlap_score(
                " ".join(
                    [
                        phrase_title,
                        phrase_coverage,
                        phrase_toc,
                        phrase_summary,
                        _phrase_ready_text(discipline_text),
                    ]
                ),
                sorted(ANTIMICROBIAL_BOOK_HINTS),
            )
            score += hint_overlap * 2.2
            if book.resource_type in {"guideline", "manual", "handbook", "reference"}:
                score += 1.6
                reasons.append("regimen_reference")
            elif book.resource_type == "textbook":
                score -= 0.6
            if book.primary_discipline in {
                "infectious_disease",
                "general_reference",
                "pharmacology",
                "primary_care",
                "pulmonology",
            }:
                score += 1.2
                reasons.append("clinical_regimen_discipline")

        negative_text = " ".join(book.negative_scope).lower()
        if negative_text and _token_overlap_score(negative_text, query_terms) > 0:
            score -= 0.8
            reasons.append("negative_scope")
    else:
        if book.evidence_tier == "textbook":
            score += 0.2

    if book.year:
        score += min(0.6, max(0.0, (book.year - 2000) / 100.0))

    if not reasons:
        if _token_overlap_score(coverage_text, query_terms) > 0:
            reasons.append("coverage")
        elif _token_overlap_score(toc_text, query_terms) > 0:
            reasons.append("contents")
        elif _token_overlap_score(summary_text, query_terms) > 0:
            reasons.append("summary")

    return score, reasons[:3]


def shortlist_local_corpus_books(
    *,
    query: str,
    domain: Optional[str] = None,
    disciplines: Optional[list[str]] = None,
    max_books: int = 5,
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_local_corpus_registry(config_or_path)
    bounded_max_books = max(1, min(5, int(max_books or 5)))

    routed = _route_domain(query, registry)
    selected_domain = _normalize_name(domain) if domain else None
    if not selected_domain:
        selected_domain = routed.get("selected_domain")

    if not selected_domain:
        domain_payload = list_local_corpus_domains(config_or_path)
        return {
            "status": "ok",
            "phase": "awaiting_domain_selection",
            "next_action": "select_domain",
            "query": query,
            "routing": routed,
            "domains": domain_payload.get("domains", []),
            "items": [],
        }

    books = list(registry.usable_books_by_domain.get(selected_domain, []))
    if not books:
        return {
            "status": "error",
            "error": f"Unknown or unavailable domain: {selected_domain}",
            "phase": "error",
            "query": query,
            "domain": selected_domain,
            "items": [],
        }

    normalized_disciplines = {
        _normalize_name(value)
        for value in (disciplines or [])
        if _normalize_name(value)
    }
    if normalized_disciplines:
        books = [
            book for book in books if book.primary_discipline in normalized_disciplines
        ]

    scored: list[tuple[float, LocalCorpusBook, list[str]]] = []
    for book in books:
        score, reasons = _book_score(book, query, selected_domain)
        scored.append((score, book, reasons))

    scored.sort(
        key=lambda item: (
            -item[0],
            -(item[1].year or 0),
            item[1].title.lower(),
        )
    )

    items = []
    for score, book, reasons in scored[:bounded_max_books]:
        items.append(
            {
                "domain": book.domain,
                "discipline": book.primary_discipline,
                "book_id": book.book_id,
                "title": book.title,
                "resource_type": book.resource_type,
                "evidence_tier": book.evidence_tier,
                "authority_or_publisher": book.authority_or_publisher,
                "year": book.year,
                "score": round(score, 4),
                "rationale": reasons,
                "coverage_phrases": list(book.coverage_phrases[:5]),
            }
        )

    return {
        "status": "ok",
        "phase": "completed",
        "next_action": "view_book_cards" if items else "refine_query",
        "query": query,
        "domain": selected_domain,
        "routing": routed,
        "disciplines": sorted(normalized_disciplines),
        "items": items,
        "candidate_count": len(items),
    }


def view_local_corpus_book_cards(
    *,
    book_ids: list[str],
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_local_corpus_registry(config_or_path)
    normalized_ids = [str(book_id).strip() for book_id in (book_ids or []) if str(book_id).strip()]
    cards = []
    for book_id in normalized_ids:
        book = registry.books_by_id.get(book_id)
        if not book or not book.usable:
            continue
        content = ""
        if book.card_path and book.card_path.exists():
            content = book.card_path.read_text(encoding="utf-8", errors="replace")
        cards.append(
            {
                "domain": book.domain,
                "discipline": book.primary_discipline,
                "book_id": book.book_id,
                "title": book.title,
                "resource_type": book.resource_type,
                "evidence_tier": book.evidence_tier,
                "authority_or_publisher": book.authority_or_publisher,
                "year": book.year,
                "content": content,
            }
        )
    return {
        "status": "ok",
        "items": cards,
        "candidate_count": len(cards),
    }


def _sqlite_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _domain_db_path(domain: str) -> Path:
    LOCAL_CORPUS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_CORPUS_INDEX_DIR / f"{domain}.sqlite"


def _domain_lock(domain: str) -> threading.Lock:
    with _REGISTRY_LOCK:
        lock = _INDEX_LOCKS.get(domain)
        if lock is None:
            lock = threading.Lock()
            _INDEX_LOCKS[domain] = lock
        return lock


def _init_domain_db(conn: sqlite3.Connection) -> bool:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
            book_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            discipline TEXT NOT NULL,
            title TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            evidence_tier TEXT NOT NULL,
            authority_or_publisher TEXT,
            year INTEGER,
            document_dir TEXT,
            selected_dir TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            book_id TEXT NOT NULL,
            title TEXT NOT NULL,
            discipline TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            evidence_tier TEXT NOT NULL,
            page_no INTEGER,
            section_path TEXT,
            content TEXT NOT NULL,
            table_ids_json TEXT NOT NULL,
            figure_ids_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tables (
            table_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            book_id TEXT NOT NULL,
            page_no INTEGER,
            section_path TEXT,
            preferred_format TEXT NOT NULL,
            preferred_path TEXT NOT NULL,
            available_formats_json TEXT NOT NULL,
            PRIMARY KEY (book_id, table_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS figures (
            figure_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            book_id TEXT NOT NULL,
            page_no INTEGER,
            section_path TEXT,
            caption TEXT,
            bbox_json TEXT,
            PRIMARY KEY (book_id, figure_id)
        )
        """
    )

    fts_enabled = True
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(
                chunk_id UNINDEXED,
                book_id UNINDEXED,
                title,
                section_path,
                content
            )
            """
        )
    except Exception as exc:
        log.warning("FTS5 unavailable for local corpus, falling back to lexical LIKE search: %s", exc)
        fts_enabled = False

    conn.commit()
    return fts_enabled


def _set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def _get_meta(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _parse_page_chunks(book: LocalCorpusBook) -> list[dict[str, Any]]:
    if not book.retrieval_path or not book.retrieval_path.exists():
        return []

    content = book.retrieval_path.read_text(encoding="utf-8", errors="replace")
    matches = list(re.finditer(r"^## Page (\d+)\s*$", content, flags=re.MULTILINE))
    chunks: list[dict[str, Any]] = []
    if not matches:
        return chunks

    for index, match in enumerate(matches):
        page_no = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        block = content[start:end].strip()
        if not block:
            continue

        section_path = ""
        table_count = 0
        figure_count = 0
        body_lines: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("Section path:"):
                section_path = _normalize_text(stripped.split(":", 1)[1])
                continue
            if stripped.startswith("Tables on this page:"):
                try:
                    table_count = int(stripped.split(":", 1)[1].strip())
                except Exception:
                    table_count = 0
                continue
            if stripped.startswith("Figures on this page:"):
                try:
                    figure_count = int(stripped.split(":", 1)[1].strip())
                except Exception:
                    figure_count = 0
                continue
            if stripped.startswith("- Figure ") or stripped == "<!-- image -->":
                continue
            if stripped == "---":
                continue
            body_lines.append(line)

        current_heading = ""
        current_parts: list[str] = []

        def flush_chunk() -> None:
            text = _normalize_text("\n".join(current_parts))
            if not text:
                return
            effective_section = section_path
            if current_heading:
                heading_text = current_heading.lstrip("#").strip()
                if heading_text and heading_text != section_path:
                    effective_section = (
                        f"{section_path} > {heading_text}" if section_path else heading_text
                    )
            chunks.append(
                {
                    "page_no": page_no,
                    "section_path": effective_section,
                    "content": text,
                    "table_count": table_count,
                    "figure_count": figure_count,
                }
            )

        for paragraph in re.split(r"\n\s*\n", "\n".join(body_lines)):
            piece = paragraph.strip()
            if not piece:
                continue
            if piece.startswith("#"):
                if current_parts:
                    flush_chunk()
                    current_parts = []
                current_heading = piece.splitlines()[0]
                current_parts.append(piece)
                continue
            candidate = current_parts + [piece]
            if len(_normalize_text("\n\n".join(candidate))) > 1800 and current_parts:
                flush_chunk()
                current_parts = [piece]
            else:
                current_parts.append(piece)

        if current_parts:
            flush_chunk()

    return chunks


def _build_table_entries(book: LocalCorpusBook) -> list[dict[str, Any]]:
    if not book.selected_dir_path:
        return []

    tables_dir = book.selected_dir_path / "tables"
    if not tables_dir.exists():
        return []

    pages_by_number: dict[int, dict[str, Any]] = {}
    catalog_path = book.selected_dir_path / "catalog.json"
    if catalog_path.exists():
        catalog = _load_json(catalog_path)
        for page in catalog.get("pages") or []:
            page_no = int(page.get("page_no") or 0)
            if page_no > 0:
                pages_by_number[page_no] = page

    document_tables = []
    document_path = book.selected_dir_path / "document.json"
    if document_path.exists():
        document = _load_json(document_path)
        document_tables = document.get("tables") or []

    by_id: dict[str, dict[str, Any]] = {}
    for table_file in tables_dir.iterdir():
        if not table_file.is_file():
            continue
        match = re.match(r"table-(\d+)\.(csv|html)$", table_file.name)
        if not match:
            continue
        table_number = int(match.group(1))
        table_id = f"table-{table_number:03d}"
        entry = by_id.setdefault(
            table_id,
            {
                "table_id": table_id,
                "available_formats": [],
                "paths": {},
                "page_no": None,
                "section_path": "",
            },
        )
        fmt = match.group(2)
        entry["available_formats"].append(fmt)
        entry["paths"][fmt] = str(table_file)
        if 0 < table_number <= len(document_tables):
            prov = (document_tables[table_number - 1].get("prov") or [{}])[0]
            page_no = int(prov.get("page_no") or 0) or None
            entry["page_no"] = page_no
            if page_no and page_no in pages_by_number:
                heading_path = pages_by_number[page_no].get("heading_path") or []
                entry["section_path"] = " > ".join(
                    _normalize_text(part) for part in heading_path if _normalize_text(part)
                )

    entries = []
    for table_id, item in sorted(by_id.items()):
        preferred_format = "csv" if "csv" in item["available_formats"] else item["available_formats"][0]
        entries.append(
            {
                "table_id": table_id,
                "page_no": item["page_no"],
                "section_path": item["section_path"],
                "preferred_format": preferred_format,
                "preferred_path": item["paths"][preferred_format],
                "available_formats": sorted(item["available_formats"]),
            }
        )
    return entries


def _build_figure_entries(book: LocalCorpusBook) -> list[dict[str, Any]]:
    if not book.selected_dir_path:
        return []

    figures_path = book.selected_dir_path / "figures.json"
    if not figures_path.exists():
        return []

    figures = _load_json(figures_path)
    entries = []
    for figure in figures or []:
        index = int(figure.get("index") or 0)
        if index <= 0:
            continue
        entries.append(
            {
                "figure_id": f"figure-{index:03d}",
                "page_no": int(figure.get("page_no") or 0) or None,
                "section_path": " > ".join(
                    _normalize_text(part)
                    for part in (figure.get("heading_path") or [])
                    if _normalize_text(part)
                ),
                "caption": _normalize_text(figure.get("caption")),
                "bbox_json": json.dumps(figure.get("bbox") or {}, ensure_ascii=False),
            }
        )
    return entries


def ensure_domain_index(domain: str, config_or_path: Any = None) -> Path:
    registry = load_local_corpus_registry(config_or_path)
    normalized_domain = _normalize_name(domain)
    if normalized_domain not in registry.usable_books_by_domain:
        raise ValueError(f"Unknown or unavailable domain: {domain}")

    db_path = _domain_db_path(normalized_domain)
    with _domain_lock(normalized_domain):
        rebuild = True
        if db_path.exists():
            try:
                with _sqlite_conn(db_path) as conn:
                    _init_domain_db(conn)
                    schema_version = _get_meta(conn, "schema_version")
                    catalog_mtime = _get_meta(conn, "catalog_mtime")
                    corpus_root = _get_meta(conn, "corpus_root")
                    rebuild = not (
                        schema_version == LOCAL_CORPUS_SCHEMA_VERSION
                        and catalog_mtime == registry.catalog_path.stat().st_mtime
                        and corpus_root == str(registry.root)
                    )
            except Exception:
                rebuild = True

        if not rebuild:
            return db_path

        if db_path.exists():
            db_path.unlink()

        with _sqlite_conn(db_path) as conn:
            fts_enabled = _init_domain_db(conn)
            books = registry.usable_books_by_domain.get(normalized_domain, [])
            for book in books:
                conn.execute(
                    """
                    INSERT INTO books (
                        book_id, domain, discipline, title, resource_type,
                        evidence_tier, authority_or_publisher, year,
                        document_dir, selected_dir
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book.book_id,
                        book.domain,
                        book.primary_discipline,
                        book.title,
                        book.resource_type,
                        book.evidence_tier,
                        book.authority_or_publisher,
                        book.year,
                        book.document_dir,
                        book.selected_dir,
                    ),
                )

                table_entries = _build_table_entries(book)
                figure_entries = _build_figure_entries(book)
                table_ids_by_page: dict[int, list[str]] = {}
                figure_ids_by_page: dict[int, list[str]] = {}

                for table in table_entries:
                    page_no = int(table.get("page_no") or 0)
                    if page_no > 0:
                        table_ids_by_page.setdefault(page_no, []).append(table["table_id"])
                    conn.execute(
                        """
                        INSERT INTO tables (
                            table_id, domain, book_id, page_no, section_path,
                            preferred_format, preferred_path, available_formats_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            table["table_id"],
                            book.domain,
                            book.book_id,
                            table.get("page_no"),
                            table.get("section_path") or "",
                            table["preferred_format"],
                            table["preferred_path"],
                            json.dumps(table["available_formats"], ensure_ascii=False),
                        ),
                    )

                for figure in figure_entries:
                    page_no = int(figure.get("page_no") or 0)
                    if page_no > 0:
                        figure_ids_by_page.setdefault(page_no, []).append(figure["figure_id"])
                    conn.execute(
                        """
                        INSERT INTO figures (
                            figure_id, domain, book_id, page_no, section_path,
                            caption, bbox_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            figure["figure_id"],
                            book.domain,
                            book.book_id,
                            figure.get("page_no"),
                            figure.get("section_path") or "",
                            figure.get("caption") or "",
                            figure.get("bbox_json") or "{}",
                        ),
                    )

                for idx, chunk in enumerate(_parse_page_chunks(book), start=1):
                    chunk_id = f"{book.book_id}:{chunk['page_no']}:{idx:03d}"
                    table_ids = table_ids_by_page.get(chunk["page_no"], [])
                    figure_ids = figure_ids_by_page.get(chunk["page_no"], [])
                    conn.execute(
                        """
                        INSERT INTO chunks (
                            chunk_id, domain, book_id, title, discipline,
                            resource_type, evidence_tier, page_no, section_path,
                            content, table_ids_json, figure_ids_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            book.domain,
                            book.book_id,
                            book.title,
                            book.primary_discipline,
                            book.resource_type,
                            book.evidence_tier,
                            chunk["page_no"],
                            chunk["section_path"],
                            chunk["content"],
                            json.dumps(table_ids, ensure_ascii=False),
                            json.dumps(figure_ids, ensure_ascii=False),
                        ),
                    )
                    if fts_enabled:
                        conn.execute(
                            """
                            INSERT INTO chunks_fts (
                                chunk_id, book_id, title, section_path, content
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                chunk_id,
                                book.book_id,
                                book.title,
                                chunk["section_path"],
                                chunk["content"],
                            ),
                        )

            _set_meta(conn, "schema_version", LOCAL_CORPUS_SCHEMA_VERSION)
            _set_meta(conn, "catalog_mtime", registry.catalog_path.stat().st_mtime)
            _set_meta(conn, "corpus_root", str(registry.root))
            _set_meta(conn, "fts_enabled", fts_enabled)
            conn.commit()

    return db_path


def _fetch_chunk_candidates(
    conn: sqlite3.Connection,
    *,
    query: str,
    domain: str,
    book_ids: list[str],
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    terms = _expanded_query_terms(query, domain)
    fts_enabled = bool(_get_meta(conn, "fts_enabled"))
    placeholders = ",".join("?" for _ in book_ids)
    rows: list[dict[str, Any]] = []

    if fts_enabled:
        try:
            fts_terms = []
            for term in terms:
                cleaned = _phrase_ready_text(term)
                if not cleaned:
                    continue
                if " " in cleaned:
                    fts_terms.append(f'"{cleaned}"')
                else:
                    fts_terms.append(cleaned)
            match_query = " OR ".join(fts_terms).strip() or "*"
            sql = f"""
                SELECT
                    c.chunk_id,
                    c.domain,
                    c.book_id,
                    c.title,
                    c.discipline,
                    c.resource_type,
                    c.evidence_tier,
                    c.page_no,
                    c.section_path,
                    c.content,
                    c.table_ids_json,
                    c.figure_ids_json,
                    bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                WHERE chunks_fts MATCH ? AND c.book_id IN ({placeholders})
                ORDER BY rank ASC
                LIMIT ?
            """
            params: list[Any] = [match_query, *book_ids, limit]
            for row in conn.execute(sql, params).fetchall():
                rows.append(dict(row))
            return rows, True
        except Exception as exc:
            log.warning("Local corpus FTS query failed, falling back to lexical scan: %s", exc)
            fts_enabled = False

    sql = f"""
        SELECT
            chunk_id,
            domain,
            book_id,
            title,
            discipline,
            resource_type,
            evidence_tier,
            page_no,
            section_path,
            content,
            table_ids_json,
            figure_ids_json
        FROM chunks
        WHERE book_id IN ({placeholders})
    """
    for row in conn.execute(sql, book_ids).fetchall():
        data = dict(row)
        lowered = str(data.get("content") or "").lower()
        lexical_hits = sum(1 for term in terms if term in lowered)
        if terms and lexical_hits == 0:
            continue
        data["rank"] = -float(lexical_hits)
        rows.append(data)

    rows.sort(key=lambda item: float(item.get("rank", 0.0)))
    return rows[:limit], False


def _retrieve_table_metadata(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    table_ids: list[str],
) -> list[dict[str, Any]]:
    if not table_ids:
        return []
    placeholders = ",".join("?" for _ in table_ids)
    sql = f"""
        SELECT table_id, page_no, section_path, preferred_format
        FROM tables
        WHERE book_id = ? AND table_id IN ({placeholders})
        ORDER BY page_no ASC, table_id ASC
    """
    params = [book_id, *table_ids]
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _retrieve_figure_metadata(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    figure_ids: list[str],
) -> list[dict[str, Any]]:
    if not figure_ids:
        return []
    placeholders = ",".join("?" for _ in figure_ids)
    sql = f"""
        SELECT figure_id, page_no, section_path, caption
        FROM figures
        WHERE book_id = ? AND figure_id IN ({placeholders})
        ORDER BY page_no ASC, figure_id ASC
    """
    params = [book_id, *figure_ids]
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _excerpt_for_query(text: str, query: str, max_chars: int = 1400) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized

    terms = _query_terms(query)
    lowered = normalized.lower()
    hit_index = min(
        [lowered.find(term) for term in terms if term in lowered] or [0]
    )
    start = max(0, hit_index - max_chars // 4)
    end = min(len(normalized), start + max_chars)
    excerpt = normalized[start:end]
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(normalized):
        excerpt = excerpt + "..."
    return excerpt


def _evidence_score(row: dict[str, Any], query: str, domain: str) -> tuple[float, list[str]]:
    terms = _expanded_query_terms(query, domain)
    phrases = _quoted_phrases(query)
    title = str(row.get("title") or "").lower()
    section = str(row.get("section_path") or "").lower()
    content = str(row.get("content") or "").lower()
    phrase_title = _phrase_ready_text(title)
    phrase_section = _phrase_ready_text(section)
    phrase_content = _phrase_ready_text(content)
    salient_phrases = _salient_query_phrases(query)
    specific_terms = _specific_query_terms(query, domain)

    lexical_hits = sum(1 for term in terms if term in content)
    lexical_score = lexical_hits / max(1, len(terms)) if terms else 0.0
    rank = float(row.get("rank") or 0.0)
    rank_score = 1.0 if rank <= 0 else 1.0 / (1.0 + rank)

    score = (lexical_score * 3.0) + rank_score
    reasons: list[str] = []

    normalized_query = _normalize_text(query).lower()
    if normalized_query and normalized_query in content:
        score += 2.4
        reasons.append("exact_content")
    if normalized_query and normalized_query in title:
        score += 2.0
        reasons.append("title")
    if normalized_query and normalized_query in section:
        score += 1.6
        reasons.append("section")

    for phrase in phrases:
        if phrase and phrase in content:
            score += 1.2
            reasons.append("quoted_phrase")

    if _token_overlap_score(section, terms) > 0:
        score += 0.8
    if _token_overlap_score(title, terms) > 0:
        score += 0.8

    for phrase in salient_phrases:
        normalized_phrase = _phrase_ready_text(phrase)
        if normalized_phrase and normalized_phrase in phrase_section:
            score += 2.2
            reasons.append("direct_topic")
        elif normalized_phrase and normalized_phrase in phrase_content:
            score += 1.8
            reasons.append("direct_topic")
        elif normalized_phrase and normalized_phrase in phrase_title:
            score += 1.0
            reasons.append("direct_topic")

    specific_hits, specific_ratio = _specificity_signal(
        " ".join([phrase_title, phrase_section, phrase_content]),
        specific_terms,
    )
    if specific_hits:
        score += specific_ratio * 2.4
        reasons.append("specificity")
    elif len(specific_terms) >= 2:
        score -= 0.8
        reasons.append("specificity_gap")

    table_ids = json.loads(row.get("table_ids_json") or "[]")
    figure_ids = json.loads(row.get("figure_ids_json") or "[]")
    if table_ids and _contains_any(set(terms), list(TABLE_LIKE_TERMS)):
        score += 0.9
        reasons.append("table_locality")
    if figure_ids and "figure" in terms:
        score += 0.3

    if domain == "medicine":
        mode = _medical_query_mode(query)
        resource_type = str(row.get("resource_type") or "")
        if mode == "management":
            if resource_type in {"guideline", "manual"}:
                score += 1.6
                reasons.append("guideline_first")
            elif resource_type == "handbook":
                score += 1.0
                reasons.append("handbook_support")
            elif resource_type == "textbook":
                score -= 0.2
        elif mode == "background":
            if resource_type == "textbook":
                score += 1.2
                reasons.append("background_textbook")

        if _has_antimicrobial_intent(query):
            regimen_signal = any(term in phrase_section for term in REGIMEN_EVIDENCE_TERMS) or any(
                term in phrase_content for term in REGIMEN_EVIDENCE_TERMS
            )
            if regimen_signal:
                score += 1.6
                reasons.append("treatment_signal")
            else:
                score -= 0.7
            if "references" in phrase_section:
                score -= 1.6
                reasons.append("references_only")

    return score, reasons[:4]


def retrieve_local_corpus_evidence(
    *,
    query: str,
    book_ids: list[str],
    top_k: int = 8,
    include_related_tables: bool = True,
    include_related_figures: bool = False,
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_local_corpus_registry(config_or_path)
    normalized_ids = [str(book_id).strip() for book_id in (book_ids or []) if str(book_id).strip()]
    if not normalized_ids:
        return {
            "status": "error",
            "error": "At least one book_id is required",
            "items": [],
        }

    books = []
    for book_id in normalized_ids:
        book = registry.books_by_id.get(book_id)
        if not book or not book.usable:
            return {
                "status": "error",
                "error": f"Unknown or unavailable book_id: {book_id}",
                "items": [],
            }
        books.append(book)

    domains = {book.domain for book in books}
    if len(domains) != 1:
        return {
            "status": "error",
            "error": "All book_ids must belong to the same domain",
            "items": [],
        }

    domain = next(iter(domains))
    db_path = ensure_domain_index(domain, config_or_path)
    bounded_top_k = max(1, min(12, int(top_k or 8)))

    with _sqlite_conn(db_path) as conn:
        _init_domain_db(conn)
        candidates, fts_enabled = _fetch_chunk_candidates(
            conn,
            query=query,
            domain=domain,
            book_ids=normalized_ids,
            limit=max(bounded_top_k * 6, bounded_top_k),
        )

        scored_items: list[dict[str, Any]] = []
        for row in candidates:
            score, reasons = _evidence_score(row, query, domain)
            table_ids = json.loads(row.get("table_ids_json") or "[]")
            figure_ids = json.loads(row.get("figure_ids_json") or "[]")
            scored_items.append(
                {
                    "domain": row["domain"],
                    "book_id": row["book_id"],
                    "title": row["title"],
                    "discipline": row["discipline"],
                    "resource_type": row["resource_type"],
                    "evidence_tier": row["evidence_tier"],
                    "page_no": row["page_no"],
                    "section_path": row["section_path"],
                    "content": _excerpt_for_query(str(row["content"]), query),
                    "score": round(score, 4),
                    "rationale": reasons,
                    "citation_label": (
                        f"{row['title']} | p. {row['page_no']} | {row['section_path']}"
                    ),
                    "related_tables": (
                        _retrieve_table_metadata(
                            conn, book_id=row["book_id"], table_ids=table_ids[:3]
                        )
                        if include_related_tables
                        else []
                    ),
                    "related_figures": (
                        _retrieve_figure_metadata(
                            conn, book_id=row["book_id"], figure_ids=figure_ids[:3]
                        )
                        if include_related_figures
                        else []
                    ),
                }
            )

    scored_items.sort(
        key=lambda item: (
            -float(item["score"]),
            item["page_no"] or 0,
            item["title"].lower(),
        )
    )
    items = scored_items[:bounded_top_k]
    direct_topic_hits = sum(
        1 for item in items if "direct_topic" in (item.get("rationale") or [])
    )
    treatment_signal_hits = sum(
        1 for item in items if "treatment_signal" in (item.get("rationale") or [])
    )
    top_score = float(items[0]["score"]) if items else 0.0
    evidence_sufficiency = "strong"
    answer_guidance = None
    if domain == "medicine":
        if not items:
            evidence_sufficiency = "weak"
            answer_guidance = (
                "No local evidence matched closely enough. Avoid answering from memory."
            )
        elif _has_antimicrobial_intent(query):
            if direct_topic_hits == 0 or treatment_signal_hits == 0 or top_score < 3.0:
                evidence_sufficiency = "weak"
                answer_guidance = (
                    "Local evidence is partial or indirect. Use cautious wording and avoid presenting any regimen as first-line unless a directly on-topic chunk states it."
                )
        elif direct_topic_hits == 0 and top_score < 2.5:
            evidence_sufficiency = "partial"
            answer_guidance = (
                "Local evidence is relevant but not tightly on-topic. Prefer scoped language and cite the best-matching page/section."
            )
    freshness_note = None
    if domain == "medicine" and _contains_any(set(_query_terms(query)), list(TIME_SENSITIVE_TERMS)):
        freshness_note = (
            "This answer is grounded in the local corpus only and may not reflect the latest guideline revision."
        )

    return {
        "status": "ok",
        "phase": "completed",
        "next_action": "answer" if items else "refine_query",
        "query": query,
        "domain": domain,
        "book_ids": normalized_ids,
        "items": items,
        "candidate_count": len(items),
        "fts_enabled": bool(fts_enabled),
        "evidence_sufficiency": evidence_sufficiency,
        "answer_guidance": answer_guidance,
        "freshness_note": freshness_note,
    }


def _parse_csv_table(path: Path) -> tuple[list[list[str]], str]:
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            cleaned = [_normalize_text(cell) for cell in row]
            if any(cleaned):
                rows.append(cleaned)
    content_text = "\n".join(" | ".join(row) for row in rows)
    return rows, content_text


def _parse_html_table(path: Path) -> tuple[list[list[str]], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows: list[list[str]] = []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        for tr in soup.find_all("tr"):
            cells = [
                _normalize_text(cell.get_text(" ", strip=True))
                for cell in tr.find_all(["th", "td"])
            ]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(cells)
        if not rows:
            paragraphs = [
                _normalize_text(node.get_text(" ", strip=True))
                for node in soup.find_all("p")
            ]
            rows = [[paragraph] for paragraph in paragraphs if paragraph]
    except Exception:
        rows = [[_normalize_text(text)]]
    content_text = "\n".join(" | ".join(row) for row in rows)
    return rows, content_text


def view_local_corpus_table(
    *,
    book_id: str,
    table_id: str,
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_local_corpus_registry(config_or_path)
    book = registry.books_by_id.get(str(book_id).strip())
    if not book or not book.usable:
        return {"status": "error", "error": f"Unknown or unavailable book_id: {book_id}"}

    db_path = ensure_domain_index(book.domain, config_or_path)
    with _sqlite_conn(db_path) as conn:
        _init_domain_db(conn)
        row = conn.execute(
            """
            SELECT page_no, section_path, preferred_format, preferred_path, available_formats_json
            FROM tables
            WHERE book_id = ? AND table_id = ?
            """,
            (book.book_id, str(table_id).strip()),
        ).fetchone()

    if not row:
        return {
            "status": "error",
            "error": f"Unknown table_id for book {book.book_id}: {table_id}",
        }

    preferred_path = Path(row["preferred_path"])
    if row["preferred_format"] == "csv":
        rows, content_text = _parse_csv_table(preferred_path)
    else:
        rows, content_text = _parse_html_table(preferred_path)

    return {
        "status": "ok",
        "domain": book.domain,
        "book_id": book.book_id,
        "title": book.title,
        "table_id": str(table_id).strip(),
        "page_no": row["page_no"],
        "section_path": row["section_path"],
        "format": row["preferred_format"],
        "available_formats": json.loads(row["available_formats_json"] or "[]"),
        "rows": rows,
        "content_text": content_text,
    }


def view_local_corpus_figure_metadata(
    *,
    book_id: str,
    figure_id: str,
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_local_corpus_registry(config_or_path)
    book = registry.books_by_id.get(str(book_id).strip())
    if not book or not book.usable:
        return {"status": "error", "error": f"Unknown or unavailable book_id: {book_id}"}

    db_path = ensure_domain_index(book.domain, config_or_path)
    with _sqlite_conn(db_path) as conn:
        _init_domain_db(conn)
        row = conn.execute(
            """
            SELECT page_no, section_path, caption, bbox_json
            FROM figures
            WHERE book_id = ? AND figure_id = ?
            """,
            (book.book_id, str(figure_id).strip()),
        ).fetchone()

    if not row:
        return {
            "status": "error",
            "error": f"Unknown figure_id for book {book.book_id}: {figure_id}",
        }

    return {
        "status": "ok",
        "domain": book.domain,
        "book_id": book.book_id,
        "title": book.title,
        "figure_id": str(figure_id).strip(),
        "page_no": row["page_no"],
        "section_path": row["section_path"],
        "caption": row["caption"],
        "bbox": json.loads(row["bbox_json"] or "{}"),
    }
