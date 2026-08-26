"""0002: 主笔规划产物列 + foreshadows 伏笔账本

- books + synopsis / worldview_json / world_rules_json
- chapters + tone / tension_curve / word_target
- scenes + stage_desc / scene_summary
- 新增 foreshadows 表（Book 级伏笔三态账本）

Revision ID: 0002_agent_columns
Revises: 0001_init_schema
Create Date: 2026-08-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_agent_columns"
down_revision: Union[str, Sequence[str], None] = "0001_init_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("books", sa.Column("synopsis", sa.Text(), nullable=False, server_default=""))
    op.add_column("books", sa.Column("worldview_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("books", sa.Column("world_rules_json", sa.Text(), nullable=False, server_default="[]"))

    op.add_column("chapters", sa.Column("tone", sa.String(20), nullable=False, server_default=""))
    op.add_column("chapters", sa.Column("tension_curve", sa.Text(), nullable=False, server_default=""))
    op.add_column("chapters", sa.Column("word_target", sa.Integer(), nullable=False, server_default="0"))

    op.add_column("scenes", sa.Column("stage_desc", sa.Text(), nullable=False, server_default=""))
    op.add_column("scenes", sa.Column("scene_summary", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "foreshadows",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("book_id", sa.String(64), sa.ForeignKey("books.id"), nullable=False, index=True),
        sa.Column("type", sa.String(20), nullable=False, server_default="plot"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="buried"),
        sa.Column("buried_scene", sa.String(64), nullable=False, server_default=""),
        sa.Column("expected_close_scene", sa.String(64), nullable=False, server_default=""),
        sa.Column("related_char_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("foreshadows")
    op.drop_column("scenes", "scene_summary")
    op.drop_column("scenes", "stage_desc")
    op.drop_column("chapters", "word_target")
    op.drop_column("chapters", "tension_curve")
    op.drop_column("chapters", "tone")
    op.drop_column("books", "world_rules_json")
    op.drop_column("books", "worldview_json")
    op.drop_column("books", "synopsis")