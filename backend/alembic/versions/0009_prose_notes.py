"""0009: 正文审计记录落库（ProseNote 实体 · 阶段④ 正文协作工作区）

多角色分工的正文协作（写手/体检员/润色师/质检员）+ 审计可追溯：
每次体检/润色/质检写一条记录（status=pending），作者 approve/reject 后才生效；
落库行为（final_prose / 伏笔推进 / 信念 / 因果）全部关联记录可回查。

Revision ID: 0009_prose_notes
Revises: 0008_scene_content
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_prose_notes"
down_revision: Union[str, Sequence[str], None] = "0008_scene_content"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prose_notes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scene_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="writer"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("suggestion", sa.Text(), nullable=False, server_default=""),
        sa.Column("before", sa.Text(), nullable=False, server_default=""),
        sa.Column("after", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=16), nullable=False, server_default="writer"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prose_notes_scene_id", "prose_notes", ["scene_id"])


def downgrade() -> None:
    op.drop_index("ix_prose_notes_scene_id", table_name="prose_notes")
    op.drop_table("prose_notes")