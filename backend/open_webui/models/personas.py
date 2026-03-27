import logging
import time
import uuid
from copy import deepcopy
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, Float, Index, String, Text
from sqlalchemy.orm import Session

from open_webui.internal.db import JSONField, Base, get_db_context

log = logging.getLogger(__name__)


def _normalize_partner_profile_text(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalize_partner_profile_list(values: Optional[list[str]]) -> list[str]:
    if not isinstance(values, list):
        return []

    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value:
            normalized.append(value)
    return normalized


class PersonaPartnerProfile(BaseModel):
    enabled: bool = False
    title: Optional[str] = None
    summary: str = ""
    relational_frame: Optional[str] = None
    style_preferences: list[str] = Field(default_factory=list)
    avoidances: list[str] = Field(default_factory=list)
    updated_at: Optional[int] = None

    model_config = ConfigDict(extra="ignore")


def normalize_partner_profile(
    partner_profile: Optional[PersonaPartnerProfile | dict[str, Any]],
    updated_at: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    if partner_profile is None:
        return None

    if isinstance(partner_profile, PersonaPartnerProfile):
        data = partner_profile.model_dump()
    elif isinstance(partner_profile, dict):
        data = deepcopy(partner_profile)
    else:
        return None

    title = _normalize_partner_profile_text(data.get("title"))
    summary = _normalize_partner_profile_text(data.get("summary")) or ""
    relational_frame = _normalize_partner_profile_text(data.get("relational_frame"))
    style_preferences = _normalize_partner_profile_list(data.get("style_preferences"))
    avoidances = _normalize_partner_profile_list(data.get("avoidances"))
    enabled = bool(data.get("enabled"))

    has_content = bool(
        title or summary or relational_frame or style_preferences or avoidances
    )
    if not enabled and not has_content:
        return None

    return {
        "enabled": enabled,
        "title": title,
        "summary": summary,
        "relational_frame": relational_frame,
        "style_preferences": style_preferences,
        "avoidances": avoidances,
        "updated_at": updated_at,
    }


class Persona(Base):
    __tablename__ = "persona"

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String, nullable=False)

    name = Column(Text, nullable=False)
    emoji = Column(Text, nullable=True)
    profile_image_url = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    archetype = Column(Text, nullable=False, server_default="assistant")

    bound_model_id = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    greeting = Column(Text, nullable=True)
    partner_profile = Column(JSONField, nullable=True)

    voice_id = Column(Text, nullable=True)
    voice_speed = Column(Float, nullable=True)

    tool_ids = Column(JSONField, nullable=True)
    skill_ids = Column(JSONField, nullable=True)
    filter_ids = Column(JSONField, nullable=True)
    action_ids = Column(JSONField, nullable=True)
    default_feature_ids = Column(JSONField, nullable=True)
    capabilities = Column(JSONField, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    updated_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("persona_user_updated_idx", "user_id", "updated_at"),
        Index("persona_user_active_idx", "user_id", "is_active"),
    )


class PersonaModel(BaseModel):
    id: str
    user_id: str

    name: str
    emoji: Optional[str] = None
    profile_image_url: Optional[str] = None
    description: Optional[str] = None
    archetype: Literal["assistant", "storyteller", "companion", "coach"] = "assistant"

    bound_model_id: Optional[str] = None
    system_prompt: Optional[str] = None
    greeting: Optional[str] = None
    partner_profile: Optional[PersonaPartnerProfile] = None

    voice_id: Optional[str] = None
    voice_speed: Optional[float] = None

    tool_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    filter_ids: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    default_feature_ids: list[str] = Field(default_factory=list)
    capabilities: dict = Field(default_factory=dict)

    is_active: bool = True
    updated_at: int
    created_at: int

    model_config = ConfigDict(from_attributes=True)


class PersonaForm(BaseModel):
    id: Optional[str] = None

    name: str
    emoji: Optional[str] = None
    profile_image_url: Optional[str] = None
    description: Optional[str] = None
    archetype: Literal["assistant", "storyteller", "companion", "coach"] = "assistant"

    bound_model_id: Optional[str] = None
    system_prompt: Optional[str] = None
    greeting: Optional[str] = None
    partner_profile: Optional[PersonaPartnerProfile] = None

    voice_id: Optional[str] = None
    voice_speed: Optional[float] = None

    tool_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    filter_ids: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    default_feature_ids: list[str] = Field(default_factory=list)
    capabilities: dict = Field(default_factory=dict)

    is_active: bool = True


class PersonaListResponse(BaseModel):
    items: list[PersonaModel]
    total: int


class PersonasTable:
    def insert_new_persona(
        self, user_id: str, form_data: PersonaForm, db: Optional[Session] = None
    ) -> Optional[PersonaModel]:
        try:
            with get_db_context(db) as db:
                now = int(time.time())
                payload = form_data.model_dump()
                payload["partner_profile"] = normalize_partner_profile(
                    form_data.partner_profile, updated_at=now
                )
                result = Persona(
                    **{
                        **payload,
                        "id": form_data.id or str(uuid.uuid4()),
                        "user_id": user_id,
                        "updated_at": now,
                        "created_at": now,
                    }
                )
                db.add(result)
                db.commit()
                db.refresh(result)
                return PersonaModel.model_validate(result)
        except Exception as exc:
            log.exception("Failed to insert a new persona: %s", exc)
            return None

    def get_persona_by_id(
        self, id: str, db: Optional[Session] = None
    ) -> Optional[PersonaModel]:
        try:
            with get_db_context(db) as db:
                result = db.get(Persona, id)
                return PersonaModel.model_validate(result) if result else None
        except Exception:
            return None

    def get_persona_by_id_and_user_id(
        self, id: str, user_id: str, db: Optional[Session] = None
    ) -> Optional[PersonaModel]:
        try:
            with get_db_context(db) as db:
                result = db.query(Persona).filter_by(id=id, user_id=user_id).first()
                return PersonaModel.model_validate(result) if result else None
        except Exception:
            return None

    def get_personas_by_user_id(
        self, user_id: str, db: Optional[Session] = None
    ) -> list[PersonaModel]:
        with get_db_context(db) as db:
            results = (
                db.query(Persona)
                .filter_by(user_id=user_id)
                .order_by(Persona.updated_at.desc(), Persona.id)
                .all()
            )
            return [PersonaModel.model_validate(result) for result in results]

    def update_persona_by_id(
        self, id: str, user_id: str, form_data: PersonaForm, db: Optional[Session] = None
    ) -> Optional[PersonaModel]:
        try:
            with get_db_context(db) as db:
                persona = db.query(Persona).filter_by(id=id, user_id=user_id).first()
                if persona is None:
                    return None

                now = int(time.time())
                payload = form_data.model_dump(exclude={"id"})
                payload["partner_profile"] = normalize_partner_profile(
                    form_data.partner_profile, updated_at=now
                )

                for key, value in payload.items():
                    setattr(persona, key, value)
                persona.updated_at = now
                db.commit()
                db.refresh(persona)
                return PersonaModel.model_validate(persona)
        except Exception as exc:
            log.exception("Failed to update persona %s: %s", id, exc)
            return None

    def toggle_persona_by_id(
        self, id: str, user_id: str, db: Optional[Session] = None
    ) -> Optional[PersonaModel]:
        try:
            with get_db_context(db) as db:
                persona = db.query(Persona).filter_by(id=id, user_id=user_id).first()
                if persona is None:
                    return None

                persona.is_active = not bool(persona.is_active)
                persona.updated_at = int(time.time())
                db.commit()
                db.refresh(persona)
                return PersonaModel.model_validate(persona)
        except Exception:
            return None

    def duplicate_persona_by_id(
        self, id: str, user_id: str, db: Optional[Session] = None
    ) -> Optional[PersonaModel]:
        try:
            with get_db_context(db) as db:
                persona = db.query(Persona).filter_by(id=id, user_id=user_id).first()
                if persona is None:
                    return None

                form = PersonaForm(
                    **{
                        **PersonaModel.model_validate(persona).model_dump(
                            exclude={"id", "user_id", "updated_at", "created_at"}
                        ),
                        "name": f"{persona.name} (Copy)",
                        "is_active": True,
                    }
                )
                return self.insert_new_persona(user_id, form, db=db)
        except Exception as exc:
            log.exception("Failed to duplicate persona %s: %s", id, exc)
            return None


Personas = PersonasTable()
