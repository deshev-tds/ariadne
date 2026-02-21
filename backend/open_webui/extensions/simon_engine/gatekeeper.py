from __future__ import annotations

import re
from typing import Any

from open_webui.extensions.simon_engine.types import GateContext, GateDecision


RE_COMPLEX = re.compile(
    r"\b(connection|relation|difference|compare|summary|timeline|trace|сравни|връзка)\b",
    re.IGNORECASE,
)

RE_HIGH_RISK = re.compile(
    r"\b(code|key|password|auth|authentication|api key|access code|pin)\b",
    re.IGNORECASE,
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "with",
    "you",
    "your",
    "we",
    "i",
    "me",
    "my",
    "our",
    "this",
    "that",
    "these",
    "those",
    "what",
    "when",
    "where",
    "who",
    "why",
    "which",
    "how",
    "и",
    "в",
    "на",
    "за",
    "с",
    "по",
    "от",
    "какво",
    "как",
    "ти",
    "ние",
}


class RLMGatekeeper:
    def __init__(
        self,
        max_debt_ratio: float = 0.8,
        min_debt_for_check: float = 0.55,
        min_fts_hits: int = 1,
        min_query_len: int = 12,
        vector_weak_threshold: float = 0.75,
        recent_window_turns: int = 8,
    ):
        self.max_debt_ratio = max_debt_ratio
        self.min_debt_for_check = min_debt_for_check
        self.min_fts_hits = min_fts_hits
        self.min_query_len = min_query_len
        self.vector_weak_threshold = vector_weak_threshold
        self.recent_window_turns = recent_window_turns

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        if not text:
            return []
        tokens = re.findall(r"\w+", text.lower())
        return [t for t in tokens if len(t) >= 3 and t not in STOPWORDS]

    def _likely_in_recent_window(
        self, user_query: str, recent_history: list[dict[str, Any]]
    ) -> bool:
        if not user_query or not recent_history:
            return False

        query_tokens = set(self._tokenize(user_query))
        if not query_tokens:
            return False

        overlap_needed = 1 if len(query_tokens) <= 1 else min(3, len(query_tokens))
        for message in recent_history[-self.recent_window_turns :]:
            content = str(message.get("content") or "")
            message_tokens = set(self._tokenize(content))
            if len(query_tokens.intersection(message_tokens)) >= overlap_needed:
                return True

        return False

    def evaluate(
        self,
        ctx: GateContext,
        *,
        user_query: str,
        explicit_recall: bool,
        soft_recall: bool,
        recent_history: list[dict[str, Any]],
    ) -> GateDecision:
        query = (user_query or "").strip()
        query_len = len(query)

        if query_len == 0:
            return GateDecision(trigger=False, reason="empty_query", metrics={"query_len": 0})

        if self._likely_in_recent_window(query, recent_history):
            return GateDecision(
                trigger=False,
                reason="likely_in_recent_window",
                metrics={"query_len": query_len},
            )

        is_complex = bool(RE_COMPLEX.search(query))
        is_high_risk = bool(RE_HIGH_RISK.search(query))

        debt_ratio = ctx.session_tokens / max(1, ctx.window_tokens)

        vector_best = max(ctx.vector_scores) if ctx.vector_scores else 0.0
        vector_weak = vector_best < self.vector_weak_threshold
        fts_weak = ctx.fts_hit_count < self.min_fts_hits
        weak_retrieval = vector_weak and fts_weak

        metrics = {
            "debt": round(debt_ratio, 3),
            "vector_best": round(vector_best, 3),
            "fts_hits": int(ctx.fts_hit_count),
            "query_len": int(query_len),
            "explicit_recall": bool(explicit_recall),
            "soft_recall": bool(soft_recall),
            "is_complex": bool(is_complex),
            "is_high_risk": bool(is_high_risk),
            "weak_retrieval": bool(weak_retrieval),
        }

        if query_len < self.min_query_len and not soft_recall and not is_complex:
            return GateDecision(trigger=False, reason="query_too_short", metrics=metrics)

        if explicit_recall:
            return GateDecision(trigger=True, reason="explicit_recall", metrics=metrics)

        if debt_ratio >= self.max_debt_ratio and (soft_recall or is_complex):
            return GateDecision(trigger=True, reason="high_debt_override", metrics=metrics)

        if soft_recall and weak_retrieval:
            return GateDecision(trigger=True, reason="recall_with_gap", metrics=metrics)

        if is_complex and (debt_ratio >= self.min_debt_for_check or weak_retrieval):
            return GateDecision(
                trigger=True,
                reason="complex_intent_with_context_gap",
                metrics=metrics,
            )

        if is_high_risk and weak_retrieval:
            return GateDecision(trigger=True, reason="high_risk_with_gap", metrics=metrics)

        return GateDecision(trigger=False, reason="base_rag_sufficient", metrics=metrics)
