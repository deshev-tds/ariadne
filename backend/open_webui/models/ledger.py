from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Session

from open_webui.internal.fork_memory_db import (
    ForkMemoryBase,
    get_fork_db_context,
    is_fork_memory_available,
)


class LedgerEntry(ForkMemoryBase):
    __tablename__ = "ledger_entries"

    id = Column(String, primary_key=True, unique=True)
    chat_id = Column(String, index=True, nullable=False)
    ledger_kind = Column(String, index=True, nullable=False)
    entry_type = Column(String, index=True, nullable=False)
    content = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    status = Column(String, index=True, nullable=False)
    supersedes_entry_id = Column(String, nullable=True)
    source_message_ids_json = Column(JSON, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=0.0)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class LedgerInjectionState(ForkMemoryBase):
    __tablename__ = "ledger_injection_state"

    chat_id = Column(String, primary_key=True, unique=True)
    last_agentic_injected_at = Column(BigInteger, nullable=True)
    last_vibe_injected_at = Column(BigInteger, nullable=True)
    last_agentic_revision_seen = Column(BigInteger, nullable=True)
    last_vibe_revision_seen = Column(BigInteger, nullable=True)
    last_compaction_version_seen = Column(BigInteger, nullable=True)


class LedgerEvent(ForkMemoryBase):
    __tablename__ = "ledger_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, index=True, nullable=False)
    ledger_kind = Column(String, index=True, nullable=True)
    event_type = Column(String, index=True, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(BigInteger, nullable=False)


class LedgerEntryModel(BaseModel):
    id: str
    chat_id: str
    ledger_kind: str
    entry_type: str
    content: str
    rationale: Optional[str] = None
    status: str
    supersedes_entry_id: Optional[str] = None
    source_message_ids_json: list[str] = []
    confidence: float
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


class LedgerInjectionStateModel(BaseModel):
    chat_id: str
    last_agentic_injected_at: Optional[int] = None
    last_vibe_injected_at: Optional[int] = None
    last_agentic_revision_seen: Optional[int] = None
    last_vibe_revision_seen: Optional[int] = None
    last_compaction_version_seen: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class LedgerEventModel(BaseModel):
    id: int
    chat_id: str
    ledger_kind: Optional[str] = None
    event_type: str
    payload: dict[str, Any] = {}
    created_at: int

    model_config = ConfigDict(from_attributes=True)


class LedgersTable:
    SINGLETON_ENTRY_TYPES = {
        "scope",
        "action_mode",
        "confirmation_policy",
        "evidence_policy",
        "side_effect_policy",
        "tooling",
        "decision",
        "output_contract",
        "tone_profile",
        "engagement_pattern",
        "topic_gravity",
    }

    def record_event(
        self,
        chat_id: str,
        event_type: str,
        *,
        ledger_kind: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        db: Optional[Session] = None,
    ) -> Optional[LedgerEventModel]:
        with get_fork_db_context(db) as db:
            event = LedgerEvent(
                chat_id=chat_id,
                ledger_kind=ledger_kind,
                event_type=event_type,
                payload=payload or {},
                created_at=int(time.time()),
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            return LedgerEventModel.model_validate(event)

    def get_active_entries(
        self,
        chat_id: str,
        ledger_kind: str,
        db: Optional[Session] = None,
    ) -> list[LedgerEntryModel]:
        with get_fork_db_context(db) as db:
            rows = (
                db.query(LedgerEntry)
                .filter_by(chat_id=chat_id, ledger_kind=ledger_kind, status="active")
                .order_by(LedgerEntry.updated_at.desc(), LedgerEntry.created_at.desc())
                .all()
            )
            return [LedgerEntryModel.model_validate(row) for row in rows]

    def get_latest_revision(
        self,
        chat_id: str,
        ledger_kind: str,
        db: Optional[Session] = None,
    ) -> int:
        with get_fork_db_context(db) as db:
            row = (
                db.query(LedgerEntry)
                .filter_by(chat_id=chat_id, ledger_kind=ledger_kind, status="active")
                .order_by(LedgerEntry.updated_at.desc())
                .first()
            )
            return int(row.updated_at) if row and row.updated_at else 0

    def get_injection_state(
        self, chat_id: str, db: Optional[Session] = None
    ) -> LedgerInjectionStateModel:
        with get_fork_db_context(db) as db:
            row = db.get(LedgerInjectionState, chat_id)
            if row is None:
                row = LedgerInjectionState(chat_id=chat_id)
                db.add(row)
                db.commit()
                db.refresh(row)
            return LedgerInjectionStateModel.model_validate(row)

    def mark_injected(
        self,
        *,
        chat_id: str,
        ledger_kind: str,
        revision_seen: int,
        compaction_version: int,
        db: Optional[Session] = None,
    ) -> LedgerInjectionStateModel:
        with get_fork_db_context(db) as db:
            row = db.get(LedgerInjectionState, chat_id)
            if row is None:
                row = LedgerInjectionState(chat_id=chat_id)
                db.add(row)

            now = int(time.time())
            if ledger_kind == "agentic":
                row.last_agentic_injected_at = now
                row.last_agentic_revision_seen = revision_seen
            else:
                row.last_vibe_injected_at = now
                row.last_vibe_revision_seen = revision_seen

            row.last_compaction_version_seen = compaction_version or 0
            db.commit()
            db.refresh(row)
            return LedgerInjectionStateModel.model_validate(row)

    def upsert_entry(
        self,
        *,
        chat_id: str,
        ledger_kind: str,
        entry_type: str,
        content: str,
        rationale: str,
        source_message_ids: list[str],
        confidence: float,
        db: Optional[Session] = None,
    ) -> dict[str, Any]:
        with get_fork_db_context(db) as db:
            normalized_content = str(content or "").strip()
            if not normalized_content:
                return {"committed": False, "superseded": 0, "entry": None}

            active_rows = (
                db.query(LedgerEntry)
                .filter_by(
                    chat_id=chat_id,
                    ledger_kind=ledger_kind,
                    entry_type=entry_type,
                    status="active",
                )
                .order_by(LedgerEntry.updated_at.desc())
                .all()
            )

            for row in active_rows:
                if str(row.content or "").strip() == normalized_content:
                    source_ids = list(row.source_message_ids_json or [])
                    merged_ids = list(dict.fromkeys([*source_ids, *source_message_ids]))
                    row.source_message_ids_json = merged_ids
                    row.rationale = rationale or row.rationale
                    row.confidence = max(float(row.confidence or 0.0), float(confidence or 0.0))
                    row.updated_at = int(time.time())
                    db.commit()
                    db.refresh(row)
                    self.record_event(
                        chat_id,
                        "commit",
                        ledger_kind=ledger_kind,
                        payload={
                            "entry_id": row.id,
                            "entry_type": entry_type,
                            "deduped": True,
                        },
                        db=db,
                    )
                    return {
                        "committed": True,
                        "superseded": 0,
                        "entry": LedgerEntryModel.model_validate(row),
                    }

            superseded = 0
            supersedes_entry_id = None
            if entry_type in self.SINGLETON_ENTRY_TYPES:
                for row in active_rows:
                    row.status = "superseded"
                    row.updated_at = int(time.time())
                    superseded += 1
                    supersedes_entry_id = supersedes_entry_id or row.id

            now = int(time.time())
            row = LedgerEntry(
                id=str(uuid.uuid4()),
                chat_id=chat_id,
                ledger_kind=ledger_kind,
                entry_type=entry_type,
                content=normalized_content,
                rationale=rationale,
                status="active",
                supersedes_entry_id=supersedes_entry_id,
                source_message_ids_json=list(dict.fromkeys(source_message_ids)),
                confidence=float(confidence or 0.0),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)

            if superseded:
                self.record_event(
                    chat_id,
                    "supersede",
                    ledger_kind=ledger_kind,
                    payload={
                        "entry_type": entry_type,
                        "count": superseded,
                        "new_entry_id": row.id,
                    },
                    db=db,
                )

            self.record_event(
                chat_id,
                "commit",
                ledger_kind=ledger_kind,
                payload={
                    "entry_id": row.id,
                    "entry_type": entry_type,
                    "deduped": False,
                },
                db=db,
            )

            return {
                "committed": True,
                "superseded": superseded,
                "entry": LedgerEntryModel.model_validate(row),
            }

    def delete_entries_for_chat_id(
        self, chat_id: str, db: Optional[Session] = None
    ) -> None:
        if not is_fork_memory_available():
            return
        with get_fork_db_context(db) as db:
            db.query(LedgerEntry).filter_by(chat_id=chat_id).delete()
            db.query(LedgerInjectionState).filter_by(chat_id=chat_id).delete()
            db.query(LedgerEvent).filter_by(chat_id=chat_id).delete()
            db.commit()

    def delete_entries_for_chat_ids(
        self, chat_ids: list[str], db: Optional[Session] = None
    ) -> None:
        if not chat_ids or not is_fork_memory_available():
            return
        with get_fork_db_context(db) as db:
            db.query(LedgerEntry).filter(LedgerEntry.chat_id.in_(chat_ids)).delete(
                synchronize_session=False
            )
            db.query(LedgerInjectionState).filter(
                LedgerInjectionState.chat_id.in_(chat_ids)
            ).delete(synchronize_session=False)
            db.query(LedgerEvent).filter(LedgerEvent.chat_id.in_(chat_ids)).delete(
                synchronize_session=False
            )
            db.commit()


Ledgers = LedgersTable()
