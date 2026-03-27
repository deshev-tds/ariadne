"""Add persona table and chat persona binding

Revision ID: c8d9e0f1a2b3
Revises: b2c3d4e5f6a7
Create Date: 2026-03-27 16:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from open_webui.migrations.util import get_existing_tables

revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_existing_indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _get_existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if "persona" not in existing_tables:
        op.create_table(
            "persona",
            sa.Column("id", sa.String(), nullable=False, primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("emoji", sa.Text(), nullable=True),
            sa.Column("profile_image_url", sa.Text(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("archetype", sa.Text(), nullable=False, server_default="assistant"),
            sa.Column("bound_model_id", sa.Text(), nullable=True),
            sa.Column("system_prompt", sa.Text(), nullable=True),
            sa.Column("greeting", sa.Text(), nullable=True),
            sa.Column("voice_id", sa.Text(), nullable=True),
            sa.Column("voice_speed", sa.Float(), nullable=True),
            sa.Column("tool_ids", sa.JSON(), nullable=True),
            sa.Column("skill_ids", sa.JSON(), nullable=True),
            sa.Column("filter_ids", sa.JSON(), nullable=True),
            sa.Column("action_ids", sa.JSON(), nullable=True),
            sa.Column("default_feature_ids", sa.JSON(), nullable=True),
            sa.Column("capabilities", sa.JSON(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
        )

    persona_indexes = _get_existing_indexes("persona")
    if "persona_user_updated_idx" not in persona_indexes:
        op.create_index("persona_user_updated_idx", "persona", ["user_id", "updated_at"])
    if "persona_user_active_idx" not in persona_indexes:
        op.create_index("persona_user_active_idx", "persona", ["user_id", "is_active"])

    if "chat" in existing_tables:
        chat_columns = _get_existing_columns("chat")
        if "persona_id" not in chat_columns:
            op.add_column("chat", sa.Column("persona_id", sa.Text(), nullable=True))

        chat_indexes = _get_existing_indexes("chat")
        if "persona_id_user_id_idx" not in chat_indexes:
            op.create_index("persona_id_user_id_idx", "chat", ["persona_id", "user_id"])


def downgrade() -> None:
    existing_tables = set(get_existing_tables())

    if "chat" in existing_tables:
        chat_indexes = _get_existing_indexes("chat")
        if "persona_id_user_id_idx" in chat_indexes:
            op.drop_index("persona_id_user_id_idx", table_name="chat")

        chat_columns = _get_existing_columns("chat")
        if "persona_id" in chat_columns:
            op.drop_column("chat", "persona_id")

    if "persona" in existing_tables:
        persona_indexes = _get_existing_indexes("persona")
        if "persona_user_active_idx" in persona_indexes:
            op.drop_index("persona_user_active_idx", table_name="persona")
        if "persona_user_updated_idx" in persona_indexes:
            op.drop_index("persona_user_updated_idx", table_name="persona")

        op.drop_table("persona")
