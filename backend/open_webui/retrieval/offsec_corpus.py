import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from open_webui.retrieval.corpus_runtime import resolve_offsec_corpus_root

log = logging.getLogger(__name__)

OFFSEC_QUERY_STOP_TERMS = {
    "about",
    "also",
    "and",
    "are",
    "best",
    "check",
    "doing",
    "during",
    "for",
    "from",
    "give",
    "help",
    "here",
    "into",
    "just",
    "like",
    "maybe",
    "more",
    "need",
    "perform",
    "please",
    "should",
    "some",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "they",
    "this",
    "uses",
    "using",
    "want",
    "what",
    "when",
    "with",
    "work",
    "workflow",
    "would",
    "your",
}
OFFSEC_DOCS_HINT_TERMS = {
    "api",
    "apis",
    "cli",
    "command",
    "commands",
    "config",
    "configuration",
    "flag",
    "flags",
    "option",
    "options",
    "parameter",
    "parameters",
    "syntax",
    "usage",
    "version",
}
OFFSEC_START_PHASES = {
    "first_pass",
    "firstpass",
    "initial",
    "initial_pass",
    "orientation",
    "plan",
    "planning",
    "start",
}
OFFSEC_PAGE_SPLIT_RE = re.compile(r"^## Page (\d+)\s*$", re.MULTILINE)

_REGISTRY_CACHE: dict[tuple[str, float, float], "OffsecRegistry"] = {}
_REGISTRY_LOCK = threading.Lock()


def clear_offsec_corpus_caches() -> None:
    with _REGISTRY_LOCK:
        _REGISTRY_CACHE.clear()


@dataclass(frozen=True)
class OffsecCard:
    item_id: str
    title: str
    kind: str
    path: Path
    markdown: str


@dataclass(frozen=True)
class OffsecBook:
    book_id: str
    title: str
    source_pdf: str
    primary_domain: str
    task_tags: tuple[str, ...]
    tool_tags: tuple[str, ...]
    platform_tags: tuple[str, ...]
    artifact_style: tuple[str, ...]
    one_liner: str
    what_it_is: str
    use_when: tuple[str, ...]
    strong_for: tuple[str, ...]
    weak_for: tuple[str, ...]
    notes: tuple[str, ...]
    card: OffsecCard
    source_path: str
    retrieval_path: Path
    raw_path: Optional[Path]
    catalog_path: Optional[Path]
    manifest_path: Optional[Path]


@dataclass
class OffsecRegistry:
    root: Path
    taxonomy_path: Path
    review_path: Path
    domains: dict[str, dict[str, Any]]
    tasks: dict[str, dict[str, Any]]
    tools: dict[str, dict[str, Any]]
    domain_cards: dict[str, OffsecCard]
    task_cards: dict[str, OffsecCard]
    tool_cards: dict[str, OffsecCard]
    books_by_id: dict[str, OffsecBook]


def _normalize_lookup(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_./+-]{1,}", str(query or "").lower()):
        normalized = term.strip().lower()
        if (
            len(normalized) < 3
            or normalized in OFFSEC_QUERY_STOP_TERMS
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        terms.append(normalized)
    return terms


def _token_hits(text: str, terms: list[str]) -> int:
    lowered = str(text or "").lower()
    if not lowered or not terms:
        return 0
    return sum(1 for term in terms if term in lowered)


def _contains_docs_hint(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(term in lowered for term in OFFSEC_DOCS_HINT_TERMS)


def _relative_source_path(source_path: str, root: Path) -> str:
    raw = str(source_path or "").strip()
    if not raw:
        return ""

    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root.resolve()).as_posix()
        except Exception:
            return candidate.as_posix()
    return Path(raw).as_posix()


def _read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _registry_cache_key(
    root: Path, taxonomy_path: Path, review_path: Path
) -> tuple[str, float, float]:
    try:
        taxonomy_mtime = taxonomy_path.stat().st_mtime
    except Exception:
        taxonomy_mtime = 0.0
    try:
        review_mtime = review_path.stat().st_mtime
    except Exception:
        review_mtime = 0.0
    return (str(root), taxonomy_mtime, review_mtime)


def _load_card(path: Path, item_id: str, title: str, kind: str) -> OffsecCard:
    if not path.exists():
        raise FileNotFoundError(f"Missing Offsec {kind} card: {path}")
    return OffsecCard(
        item_id=item_id,
        title=_normalize_text(title),
        kind=kind,
        path=path,
        markdown=_read_markdown(path),
    )


def load_offsec_registry(config_or_path: Any = None) -> OffsecRegistry:
    root = resolve_offsec_corpus_root(config_or_path)
    if root is None:
        raise FileNotFoundError("Offsec corpus root could not be resolved")

    taxonomy_path = root / "_serving" / "internal" / "offsec-taxonomy-catalog.json"
    review_path = root / "_compiled_docling_review" / "compiled-offsec-review.json"
    if not taxonomy_path.exists():
        raise FileNotFoundError(f"Missing Offsec taxonomy catalog: {taxonomy_path}")
    if not review_path.exists():
        raise FileNotFoundError(f"Missing Offsec compiled review summary: {review_path}")

    cache_key = _registry_cache_key(root, taxonomy_path, review_path)
    with _REGISTRY_LOCK:
        cached = _REGISTRY_CACHE.get(cache_key)
        if cached is not None:
            return cached

        taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8", errors="replace"))
        review_summary = json.loads(
            review_path.read_text(encoding="utf-8", errors="replace")
        )

        review_items = review_summary.get("review_queue") or []
        review_by_source: dict[str, dict[str, Any]] = {}
        for item in review_items:
            if not isinstance(item, dict):
                continue
            source_key = _relative_source_path(item.get("source_path"), root)
            if source_key:
                review_by_source[source_key] = item

        domains: dict[str, dict[str, Any]] = {}
        tasks: dict[str, dict[str, Any]] = {}
        tools: dict[str, dict[str, Any]] = {}
        domain_cards: dict[str, OffsecCard] = {}
        task_cards: dict[str, OffsecCard] = {}
        tool_cards: dict[str, OffsecCard] = {}
        books_by_id: dict[str, OffsecBook] = {}

        for item in taxonomy.get("domains") or []:
            domain_id = str(item.get("id") or "").strip()
            if not domain_id:
                continue
            domains[domain_id] = item
            domain_cards[domain_id] = _load_card(
                root / "_serving" / "domains" / f"{domain_id}.md",
                domain_id,
                item.get("title") or domain_id,
                "domain",
            )

        for item in taxonomy.get("tasks") or []:
            task_id = str(item.get("id") or "").strip()
            if not task_id:
                continue
            tasks[task_id] = item
            task_cards[task_id] = _load_card(
                root / "_serving" / "tasks" / f"{task_id}.md",
                task_id,
                item.get("title") or task_id,
                "task",
            )

        for item in taxonomy.get("tools") or []:
            tool_id = str(item.get("id") or "").strip()
            if not tool_id:
                continue
            tools[tool_id] = item
            tool_cards[tool_id] = _load_card(
                root / "_serving" / "tools" / f"{tool_id}.md",
                tool_id,
                item.get("title") or tool_id,
                "tool",
            )

        missing_book_reviews: list[str] = []
        missing_book_retrieval: list[str] = []

        for item in taxonomy.get("books") or []:
            book_id = str(item.get("id") or "").strip()
            if not book_id:
                continue

            source_pdf = str(item.get("source_pdf") or "").strip()
            source_key = _relative_source_path(source_pdf, root)
            review_item = review_by_source.get(source_key)
            if review_item is None:
                missing_book_reviews.append(book_id)
                continue

            retrieval_path = Path(str(review_item.get("retrieval_markdown_path") or ""))
            if not retrieval_path.exists():
                missing_book_retrieval.append(book_id)
                continue

            book_card = _load_card(
                root / "_serving" / "books" / f"{book_id}.md",
                book_id,
                item.get("title") or book_id,
                "book",
            )
            raw_path = Path(str(review_item.get("raw_markdown_path") or ""))
            catalog_path = Path(str(review_item.get("catalog_path") or ""))
            manifest_path = Path(str(review_item.get("manifest_path") or ""))

            books_by_id[book_id] = OffsecBook(
                book_id=book_id,
                title=_normalize_text(item.get("title") or book_id),
                source_pdf=source_pdf,
                primary_domain=str(item.get("primary_domain") or "").strip(),
                task_tags=tuple(str(tag).strip() for tag in item.get("task_tags") or []),
                tool_tags=tuple(str(tag).strip() for tag in item.get("tool_tags") or []),
                platform_tags=tuple(
                    str(tag).strip() for tag in item.get("platform_tags") or []
                ),
                artifact_style=tuple(
                    str(tag).strip() for tag in item.get("artifact_style") or []
                ),
                one_liner=_normalize_text(item.get("one_liner")),
                what_it_is=_normalize_text(item.get("what_it_is")),
                use_when=tuple(_normalize_text(tag) for tag in item.get("use_when") or []),
                strong_for=tuple(
                    _normalize_text(tag) for tag in item.get("strong_for") or []
                ),
                weak_for=tuple(_normalize_text(tag) for tag in item.get("weak_for") or []),
                notes=tuple(_normalize_text(tag) for tag in item.get("notes") or []),
                card=book_card,
                source_path=str(review_item.get("source_path") or ""),
                retrieval_path=retrieval_path,
                raw_path=raw_path if raw_path.exists() else None,
                catalog_path=catalog_path if catalog_path.exists() else None,
                manifest_path=manifest_path if manifest_path.exists() else None,
            )

        if missing_book_reviews:
            raise ValueError(
                "Missing compiled review entries for Offsec book ids: "
                + ", ".join(sorted(missing_book_reviews))
            )
        if missing_book_retrieval:
            raise ValueError(
                "Missing retrieval payloads for Offsec book ids: "
                + ", ".join(sorted(missing_book_retrieval))
            )

        registry = OffsecRegistry(
            root=root,
            taxonomy_path=taxonomy_path,
            review_path=review_path,
            domains=domains,
            tasks=tasks,
            tools=tools,
            domain_cards=domain_cards,
            task_cards=task_cards,
            tool_cards=tool_cards,
            books_by_id=books_by_id,
        )
        _REGISTRY_CACHE.clear()
        _REGISTRY_CACHE[cache_key] = registry
        return registry


def _exact_entity_match(
    registry: OffsecRegistry, entity: str
) -> tuple[str, str, str, float] | None:
    normalized = _normalize_lookup(entity)
    if not normalized:
        return None

    for task_id, task in registry.tasks.items():
        if normalized in {
            _normalize_lookup(task_id),
            _normalize_lookup(task.get("title")),
        }:
            return ("task", task_id, str(task.get("title") or task_id), 10.0)

    for tool_id, tool in registry.tools.items():
        if normalized in {
            _normalize_lookup(tool_id),
            _normalize_lookup(tool.get("title")),
        }:
            return ("tool", tool_id, str(tool.get("title") or tool_id), 10.0)

    for domain_id, domain in registry.domains.items():
        if normalized in {
            _normalize_lookup(domain_id),
            _normalize_lookup(domain.get("title")),
        }:
            return ("domain", domain_id, str(domain.get("title") or domain_id), 10.0)

    for book_id, book in registry.books_by_id.items():
        if normalized in {
            _normalize_lookup(book_id),
            _normalize_lookup(book.title),
        }:
            return ("direct_lookup", book_id, book.title, 10.0)

    return None


def _card_score(card: OffsecCard, query: str, terms: list[str], anchor: str = "") -> float:
    title_text = card.title.lower()
    markdown_text = card.markdown.lower()
    score = 0.0
    score += _token_hits(title_text, terms) * 3.0
    score += _token_hits(markdown_text, terms) * 1.0

    lowered_query = _normalize_text(query).lower()
    if lowered_query and lowered_query in markdown_text:
        score += 2.2

    lowered_anchor = _normalize_text(anchor).lower()
    if lowered_anchor:
        if lowered_anchor in title_text:
            score += 3.2
        elif lowered_anchor in markdown_text:
            score += 1.8

    return score


def _route_consultation(
    registry: OffsecRegistry,
    *,
    objective: str,
    phase: str,
    current_findings: str,
    current_hypothesis: str,
    named_entity: str,
) -> tuple[str, str, str, float]:
    exact = _exact_entity_match(registry, named_entity)
    if exact is not None:
        return exact

    query = " ".join(
        value
        for value in [named_entity, objective, current_findings, current_hypothesis]
        if value
    )
    terms = _query_terms(query)
    if not terms:
        return ("direct_lookup", "", _normalize_text(named_entity or objective), 0.0)

    task_scores = [
        (
            _card_score(task_card, objective or query, terms, named_entity),
            task_id,
            task_card.title,
        )
        for task_id, task_card in registry.task_cards.items()
    ]
    tool_scores = [
        (
            _card_score(tool_card, named_entity or objective or query, terms, named_entity),
            tool_id,
            tool_card.title,
        )
        for tool_id, tool_card in registry.tool_cards.items()
    ]
    domain_scores = [
        (
            _card_score(domain_card, objective or query, terms, named_entity),
            domain_id,
            domain_card.title,
        )
        for domain_id, domain_card in registry.domain_cards.items()
    ]

    best_task = max(task_scores, default=(0.0, "", ""))
    best_tool = max(tool_scores, default=(0.0, "", ""))
    best_domain = max(domain_scores, default=(0.0, "", ""))

    phase_name = _normalize_name(phase)
    if phase_name in OFFSEC_START_PHASES and best_task[0] > 0:
        if best_task[0] >= best_tool[0] - 0.6 and best_task[0] >= best_domain[0] - 0.4:
            return ("task", best_task[1], best_task[2], round(best_task[0], 4))

    best_kind = max(
        [("task",) + best_task, ("tool",) + best_tool, ("domain",) + best_domain],
        key=lambda item: (item[1], item[0]),
    )
    if float(best_kind[1]) <= 0.0:
        return ("direct_lookup", "", _normalize_text(named_entity or objective), 0.0)

    return (
        best_kind[0],
        best_kind[2],
        best_kind[3],
        round(float(best_kind[1]), 4),
    )


def _books_for_route(
    registry: OffsecRegistry,
    route: str,
    matched_id: str,
) -> list[OffsecBook]:
    if route == "task":
        task = registry.tasks.get(matched_id) or {}
        return [
            registry.books_by_id[book_id]
            for book_id in task.get("books") or []
            if book_id in registry.books_by_id
        ]

    if route == "tool":
        tool = registry.tools.get(matched_id) or {}
        return [
            registry.books_by_id[book_id]
            for book_id in tool.get("books") or []
            if book_id in registry.books_by_id
        ]

    if route == "domain":
        return [
            book for book in registry.books_by_id.values() if book.primary_domain == matched_id
        ]

    if route == "direct_lookup" and matched_id in registry.books_by_id:
        return [registry.books_by_id[matched_id]]

    return []


def _render_preview_item(item: dict[str, Any]) -> str:
    heading = f"### {item.get('title', 'Offsec evidence')}"
    if item.get("page_no"):
        heading += f" - page {item['page_no']}"
    section_path = _normalize_text(item.get("section_path"))
    parts = [heading]
    if section_path:
        parts.append(f"Section path: {section_path}")
    parts.append(str(item.get("content") or "").strip())
    return "\n\n".join(part for part in parts if part)


def _trim_excerpt(text: str, *, max_chars: int = 1200) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _selection_markdown(
    *,
    route: str,
    matched_title: str,
    selected_card: Optional[OffsecCard],
    books: list[OffsecBook],
    preview_items: Optional[list[dict[str, Any]]] = None,
    direct_lookup_note: Optional[str] = None,
) -> str:
    parts = ["# Offsec Consultation", f"Selected route: `{route}`"]
    if matched_title:
        parts.append(f"Matched target: {matched_title}")
    if direct_lookup_note:
        parts.append(direct_lookup_note)
    if selected_card is not None:
        parts.extend(["## Selected card", selected_card.markdown])
    if books:
        parts.append("## Recommended books")
        for book in books[:3]:
            parts.append(book.card.markdown)
    if preview_items:
        parts.append("## Preview evidence")
        for item in preview_items[:3]:
            parts.append(_render_preview_item(item))
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def _recommended_next_steps(
    *,
    route: str,
    docs_fallback_suggested: bool,
) -> list[str]:
    if route == "task":
        steps = [
            "Use the selected task slice to shape the next terminal pass and keep the first sweep lightweight.",
            "If you need local examples or validation patterns, call offsec_retrieve_evidence on the top recommended books.",
            "Re-consult with new findings only if the target picture or working hypothesis materially changes.",
        ]
    elif route == "tool":
        steps = [
            "Use the selected tool slice to place the tool inside the workflow rather than treating it as the whole method.",
            "Pull deeper local examples with offsec_retrieve_evidence before committing to a narrower path.",
            "Re-consult only if the tool fit changes or the target shape becomes clearer mid-run.",
        ]
    elif route == "domain":
        steps = [
            "Use the selected domain slice to pick the next task-shaped move instead of browsing broadly.",
            "Open one or two recommended books for methodology or examples before deep evidence retrieval.",
            "Re-consult if you need to narrow from the domain into a more specific tool or task lane.",
        ]
    else:
        steps = [
            "Use the preview snippets to decide whether the named entity is locally covered well enough to continue from the corpus.",
            "If you need more examples, call offsec_retrieve_evidence with the recommended book ids.",
            "If local evidence stays thin, pivot to official or project documentation before broad web search.",
        ]

    if docs_fallback_suggested and route != "direct_lookup":
        steps[-1] = (
            "If exact syntax, flags, or version-specific behavior becomes the blocker, "
            "prefer official or project documentation before broad web search."
        )
    return steps


def _docs_topics(
    *,
    route: str,
    matched_title: str,
    named_entity: str,
    books: list[OffsecBook],
) -> list[str]:
    topics: list[str] = []
    if named_entity:
        topics.append(_normalize_text(named_entity))
    if route in {"tool", "direct_lookup"} and matched_title:
        topics.append(_normalize_text(matched_title))
    elif route == "task" and books:
        topics.append(books[0].title)

    deduped: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        normalized = topic.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(topic)
    return deduped[:3]


def _should_suggest_docs(
    *,
    route: str,
    objective: str,
    current_findings: str,
    current_hypothesis: str,
    named_entity: str,
) -> bool:
    combined = " ".join(
        value
        for value in [objective, current_findings, current_hypothesis, named_entity]
        if value
    )
    if _contains_docs_hint(combined):
        return True
    return route == "direct_lookup" and bool(_normalize_text(named_entity))


def _parse_retrieval_pages(retrieval_path: Path) -> list[dict[str, Any]]:
    text = retrieval_path.read_text(encoding="utf-8", errors="replace")
    matches = list(OFFSEC_PAGE_SPLIT_RE.finditer(text))
    pages: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        page_no = int(match.group(1))
        section_path = ""
        cleaned_lines: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("Section path:"):
                section_path = _normalize_text(stripped.split(":", 1)[1])
                continue
            if stripped.startswith("Tables on this page:"):
                continue
            if stripped.startswith("Figures on this page:"):
                continue
            if stripped.startswith("- Figure "):
                continue
            if stripped == "<!-- image -->":
                continue
            if stripped == "---":
                continue
            cleaned_lines.append(line.rstrip())

        content = "\n".join(line for line in cleaned_lines if line.strip()).strip()
        if not content or content.startswith("[Suppressed front matter page"):
            continue
        pages.append(
            {
                "page_no": page_no,
                "section_path": section_path,
                "content": _trim_excerpt(content, max_chars=1400),
            }
        )
    return pages


def _evidence_score(
    *,
    book: OffsecBook,
    page: dict[str, Any],
    query: str,
    terms: list[str],
    anchor: str = "",
) -> float:
    title_hits = _token_hits(book.title, terms)
    section_hits = _token_hits(page.get("section_path", ""), terms)
    content_hits = _token_hits(page.get("content", ""), terms)
    score = (title_hits * 0.8) + (section_hits * 1.8) + (content_hits * 1.2)

    lowered_query = _normalize_text(query).lower()
    lowered_content = str(page.get("content") or "").lower()
    lowered_anchor = _normalize_text(anchor).lower()
    if lowered_query and lowered_query in lowered_content:
        score += 2.0
    if lowered_anchor:
        if lowered_anchor in str(page.get("section_path") or "").lower():
            score += 2.2
        elif lowered_anchor in lowered_content:
            score += 1.4
    return score


def _search_offsec_evidence(
    registry: OffsecRegistry,
    *,
    query: str,
    book_ids: Optional[list[str]] = None,
    max_snippets: int = 6,
    anchor: str = "",
) -> tuple[list[dict[str, Any]], int, list[str]]:
    bounded_max_snippets = max(1, min(8, int(max_snippets or 6)))
    selected_book_ids = [
        book_id for book_id in (book_ids or []) if book_id in registry.books_by_id
    ]
    books = (
        [registry.books_by_id[book_id] for book_id in selected_book_ids]
        if selected_book_ids
        else list(registry.books_by_id.values())
    )

    terms = _query_terms(query if not anchor else f"{anchor} {query}")
    if not terms:
        return ([], 0, selected_book_ids or [])

    items: list[dict[str, Any]] = []
    for book in books:
        for page in _parse_retrieval_pages(book.retrieval_path):
            score = _evidence_score(
                book=book,
                page=page,
                query=query,
                terms=terms,
                anchor=anchor,
            )
            if score <= 0:
                continue
            items.append(
                {
                    "book_id": book.book_id,
                    "title": book.title,
                    "domain": book.primary_domain,
                    "page_no": page.get("page_no"),
                    "section_path": page.get("section_path", ""),
                    "content": page.get("content", ""),
                    "score": round(score, 4),
                    "citation_label": (
                        f"{book.title} p.{page.get('page_no')}"
                        + (
                            f" - {page.get('section_path')}"
                            if page.get("section_path")
                            else ""
                        )
                    ),
                    "source_path": str(book.retrieval_path),
                }
            )

    items.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("title") or ""),
            int(item.get("page_no") or 0),
        )
    )
    top_items = items[:bounded_max_snippets]
    result_book_ids: list[str] = []
    seen: set[str] = set()
    for item in top_items:
        book_id = str(item.get("book_id") or "")
        if book_id and book_id not in seen:
            seen.add(book_id)
            result_book_ids.append(book_id)
    return (top_items, len(items), result_book_ids)


def consult_offsec_corpus(
    *,
    objective: str,
    phase: str = "start",
    current_findings: str = "",
    current_hypothesis: str = "",
    named_entity: str = "",
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_offsec_registry(config_or_path)
    route, matched_id, matched_title, route_confidence = _route_consultation(
        registry,
        objective=objective,
        phase=phase,
        current_findings=current_findings,
        current_hypothesis=current_hypothesis,
        named_entity=named_entity,
    )

    selected_card: Optional[OffsecCard] = None
    if route == "task":
        selected_card = registry.task_cards.get(matched_id)
    elif route == "tool":
        selected_card = registry.tool_cards.get(matched_id)
    elif route == "domain":
        selected_card = registry.domain_cards.get(matched_id)

    books = _books_for_route(registry, route, matched_id)[:3]
    preview_items: list[dict[str, Any]] = []
    direct_lookup_note = None

    if route == "direct_lookup":
        lookup_anchor = _normalize_text(named_entity or matched_title or objective)
        preview_items, candidate_count, preview_book_ids = _search_offsec_evidence(
            registry,
            query=objective or lookup_anchor,
            book_ids=None,
            max_snippets=3,
            anchor=lookup_anchor,
        )
        if preview_book_ids:
            books = [
                registry.books_by_id[book_id]
                for book_id in preview_book_ids
                if book_id in registry.books_by_id
            ][:3]
        direct_lookup_note = (
            f"No curated task, tool, or domain card matched `{lookup_anchor}` cleanly. "
            "Using direct local book and evidence lookup instead."
            if lookup_anchor
            else "No curated task, tool, or domain card matched cleanly. Using direct local lookup instead."
        )
    else:
        candidate_count = 0

    docs_fallback_suggested = _should_suggest_docs(
        route=route,
        objective=objective,
        current_findings=current_findings,
        current_hypothesis=current_hypothesis,
        named_entity=named_entity,
    )

    source_documents: list[dict[str, Any]] = []
    if selected_card is not None:
        source_documents.append(
            {
                "id": f"{selected_card.kind}:{selected_card.item_id}",
                "name": selected_card.title,
                "type": f"offsec_{selected_card.kind}_card",
                "content": selected_card.markdown,
                "source_path": str(selected_card.path),
            }
        )
    for book in books:
        source_documents.append(
            {
                "id": f"book:{book.book_id}",
                "name": book.title,
                "type": "offsec_book_card",
                "content": book.card.markdown,
                "source_path": str(book.card.path),
                "book_id": book.book_id,
                "domain": book.primary_domain,
            }
        )
    for item in preview_items:
        source_documents.append(
            {
                "id": f"evidence:{item['book_id']}:{item['page_no']}",
                "name": item["title"],
                "type": "offsec_book",
                "content": item["content"],
                "book_id": item["book_id"],
                "domain": item["domain"],
                "page_no": item["page_no"],
                "section_path": item["section_path"],
                "source_path": item["source_path"],
            }
        )

    selection_markdown = _selection_markdown(
        route=route,
        matched_title=matched_title,
        selected_card=selected_card,
        books=books,
        preview_items=preview_items if route == "direct_lookup" else None,
        direct_lookup_note=direct_lookup_note,
    )

    return {
        "status": "ok",
        "phase": "consultation",
        "route": route,
        "route_confidence": route_confidence,
        "matched_id": matched_id,
        "matched_title": matched_title,
        "objective": _normalize_text(objective),
        "selection_markdown": selection_markdown,
        "recommended_book_ids": [book.book_id for book in books],
        "recommended_next_steps": _recommended_next_steps(
            route=route,
            docs_fallback_suggested=docs_fallback_suggested,
        ),
        "docs_fallback_suggested": docs_fallback_suggested,
        "docs_topics": _docs_topics(
            route=route,
            matched_title=matched_title,
            named_entity=named_entity,
            books=books,
        ),
        "preview_items": preview_items if route == "direct_lookup" else [],
        "candidate_count": candidate_count,
        "source_documents": source_documents,
    }


def retrieve_offsec_evidence(
    *,
    query: str,
    book_ids: Optional[list[str]] = None,
    max_snippets: int = 6,
    config_or_path: Any = None,
) -> dict[str, Any]:
    registry = load_offsec_registry(config_or_path)
    valid_book_ids = [
        book_id for book_id in (book_ids or []) if book_id in registry.books_by_id
    ]
    items, candidate_count, result_book_ids = _search_offsec_evidence(
        registry,
        query=query,
        book_ids=valid_book_ids or None,
        max_snippets=max_snippets,
    )
    return {
        "status": "ok",
        "phase": "evidence_gathering",
        "next_action": "synthesize" if items else "consult_or_docs_fallback",
        "query": _normalize_text(query),
        "book_ids": valid_book_ids or result_book_ids,
        "items": items,
        "candidate_count": candidate_count,
    }
