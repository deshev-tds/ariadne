from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models.access_grants import AccessGrant
from open_webui.models.skills import Skill
from open_webui.models.users import User
from open_webui.utils.science_lane import DEFAULT_SCIENCE_LANE_SKILL_IDS
from open_webui.utils.science_lane_skills import ensure_science_lane_skills


def _build_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'science-lane-skills.db'}")
    User.__table__.create(engine)
    Skill.__table__.create(engine)
    AccessGrant.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    return session


def _insert_user(session, *, user_id: str, role: str, created_at: int) -> None:
    session.add(
        User(
            id=user_id,
            email=f"{user_id}@example.com",
            username=user_id,
            role=role,
            name=user_id,
            profile_image_url="/user.png",
            bio=None,
            gender=None,
            date_of_birth=None,
            timezone=None,
            presence_state=None,
            status_emoji=None,
            status_message=None,
            status_expires_at=None,
            info=None,
            settings=None,
            oauth=None,
            scim=None,
            last_active_at=created_at,
            updated_at=created_at,
            created_at=created_at,
        )
    )
    session.commit()


def test_ensure_science_lane_skills_creates_defaults_for_earliest_admin(tmp_path):
    session = _build_session(tmp_path)
    _insert_user(session, user_id="member", role="user", created_at=10)
    _insert_user(session, user_id="admin-late", role="admin", created_at=30)
    _insert_user(session, user_id="admin-early", role="admin", created_at=20)

    report = ensure_science_lane_skills(session)

    assert report.owner_user_id == "admin-early"
    assert set(report.created_ids) == set(DEFAULT_SCIENCE_LANE_SKILL_IDS)
    assert set(report.grant_fixed_ids) == set(DEFAULT_SCIENCE_LANE_SKILL_IDS)

    skills = session.query(Skill).order_by(Skill.id.asc()).all()
    assert [skill.id for skill in skills] == sorted(DEFAULT_SCIENCE_LANE_SKILL_IDS)
    assert {skill.user_id for skill in skills} == {"admin-early"}
    assert all(skill.is_active for skill in skills)

    grants = session.query(AccessGrant).order_by(AccessGrant.resource_id.asc()).all()
    assert len(grants) == len(DEFAULT_SCIENCE_LANE_SKILL_IDS)
    assert {
        (grant.resource_type, grant.resource_id, grant.principal_type, grant.principal_id, grant.permission)
        for grant in grants
    } == {
        ("skill", skill_id, "user", "*", "read")
        for skill_id in DEFAULT_SCIENCE_LANE_SKILL_IDS
    }


def test_ensure_science_lane_skills_repairs_existing_records_and_is_idempotent(tmp_path):
    session = _build_session(tmp_path)
    _insert_user(session, user_id="admin", role="admin", created_at=10)

    session.add(
        Skill(
            id="kdense-paper-lookup",
            user_id="admin",
            name="Wrong Name",
            description="wrong description",
            content="wrong content",
            meta={"tags": ["wrong"]},
            is_active=False,
            updated_at=1,
            created_at=1,
        )
    )
    session.commit()

    first_report = ensure_science_lane_skills(session)
    second_report = ensure_science_lane_skills(session)

    assert "kdense-paper-lookup" in first_report.updated_ids
    assert "kdense-paper-lookup" in first_report.activated_ids
    assert "kdense-paper-lookup" in first_report.grant_fixed_ids
    assert set(skill.id for skill in session.query(Skill).all()) == set(
        DEFAULT_SCIENCE_LANE_SKILL_IDS
    )
    assert session.query(AccessGrant).count() == len(DEFAULT_SCIENCE_LANE_SKILL_IDS)

    repaired = session.query(Skill).filter_by(id="kdense-paper-lookup").first()
    assert repaired is not None
    assert repaired.name == "K-Dense Paper Lookup"
    assert repaired.description.startswith("Find and triage specific papers")
    assert repaired.content.startswith("# Paper Lookup")
    assert repaired.meta == {
        "tags": ["science", "kdense", "paper-lookup", "citations"]
    }
    assert repaired.is_active is True

    assert second_report.created_ids == []
    assert second_report.updated_ids == []
    assert second_report.activated_ids == []
    assert second_report.grant_fixed_ids == []


def test_ensure_science_lane_skills_skips_when_no_admin_exists(tmp_path):
    session = _build_session(tmp_path)
    _insert_user(session, user_id="member", role="user", created_at=10)

    report = ensure_science_lane_skills(session)

    assert report.owner_user_id is None
    assert report.created_ids == []
    assert session.query(Skill).count() == 0
    assert session.query(AccessGrant).count() == 0
