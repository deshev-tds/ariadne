"""Add partner profile to persona

Revision ID: d4e5f6a7b8c9
Revises: c8d9e0f1a2b3
Create Date: 2026-03-27 20:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from open_webui.migrations.util import get_existing_tables

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if "persona" in existing_tables:
        persona_columns = _get_existing_columns("persona")
        if "partner_profile" not in persona_columns:
            op.add_column("persona", sa.Column("partner_profile", sa.JSON(), nullable=True))


def downgrade() -> None:
    existing_tables = set(get_existing_tables())

    if "persona" in existing_tables:
        persona_columns = _get_existing_columns("persona")
        if "partner_profile" in persona_columns:
            op.drop_column("persona", "partner_profile")
