"""0014: prose_annotations（P2 · 段落批注）

作者选中正文段落写下意见 → 落库成「可追踪的改稿任务」（open → handled/dismissed）。
设计依据：docs/正文协作副驾与Agent协作架构.md §8.1。
幂等：表已存在则只补缺失列。

Revision ID: 0014_prose_annotations
Revises: 0013_reconcile_schema_drift
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_prose_annotations"
down_revision = "0013_reconcile_schema_drift"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if _has_table("prose_annotations"):
        return
    op.create_table(
        "prose_annotations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scene_id", sa.String(64), sa.ForeignKey("scenes.id"), nullable=False, index=True),
        sa.Column("para_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_by", sa.String(24), nullable=False, server_default="author"),
        sa.Column("handled_by_note_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    if _has_table("prose_annotations"):
        op.drop_table("prose_annotations")
