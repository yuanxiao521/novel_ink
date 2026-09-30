"""0012: world_states（S1 世界状态账本 · 战力/金钱/道具链/时间锚/术语/关键数字）

只建表、不碰任何业务逻辑（per S1 方案 §7 step2）。
后端 settle：世界状态 Upsert 语义键为 UNIQUE(book_id, kind, key)。

Revision ID: 0012_world_states
Revises: 0011_chat_histories
Create Date: 2026-09-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_world_states"
down_revision: Union[str, Sequence[str], None] = "0011_chat_histories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "world_states",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("book_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("scene_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_event_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("previous_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_world_states_book_id", "world_states", ["book_id"])
    op.create_index(
        "ux_world_states_book_kind_key", "world_states", ["book_id", "kind", "key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_world_states_book_kind_key", table_name="world_states")
    op.drop_index("ix_world_states_book_id", table_name="world_states")
    op.drop_table("world_states")