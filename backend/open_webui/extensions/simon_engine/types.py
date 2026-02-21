from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SimonTurnRequest(BaseModel):
    chat_id: str
    lineage_anchor_message_id: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    user_text: str
    user: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SimonTurnEvent(BaseModel):
    type: Literal["status", "delta", "final", "done", "error", "sse"]
    data: dict[str, Any] | str


class GateContext(BaseModel):
    session_tokens: int
    window_tokens: int
    vector_scores: list[float] = Field(default_factory=list)
    fts_hit_count: int
    query_len: int


class GateDecision(BaseModel):
    trigger: bool
    reason: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class RetrievalProbe(BaseModel):
    vector_memories: list[str] = Field(default_factory=list)
    vector_scores: list[float] = Field(default_factory=list)
    lexical_hits: list[dict[str, Any]] = Field(default_factory=list)
    lexical_lines: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class ContextAnchor(BaseModel):
    source: Literal["memory", "lexical", "warm", "hot"]
    text: str
    role: str | None = None
    message_id: str | None = None
    score: float | None = None


class HotCacheEntry(BaseModel):
    role: str
    content: str
    parent_id: str | None = None
    message_id: str | None = None
    created_at: int | None = None


class SimonRuntimeContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    chat_id: str
    lineage_anchor_message_id: str | None = None
    hot_key: str
    hot_enabled: bool = False
    hot_mode_reason: str = "disabled"
    warm_history: list[dict[str, Any]] = Field(default_factory=list)
    hot_history: list[dict[str, Any]] = Field(default_factory=list)
    branch_message_ids: list[str] = Field(default_factory=list)
    messages_map: dict[str, dict[str, Any]] = Field(default_factory=dict)
