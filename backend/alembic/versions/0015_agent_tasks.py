"""0015: agent_tasks + agent_messages（A+ 批 · 任务总线 / L3 消息层）

设计依据：docs/正文协作副驾与Agent协作架构.md §5.1/§8.2/§8.3。
只传请求/裁决/通知，不传状态；状态机 submitted→working→input-required→completed/failed。
幂等：表已存在则跳过。

Revision ID: 0015_agent_tasks
Revises: 0014_prose_annotations
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_agent_tasks"
down_revision = "0014_prose_annotations"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("agent_tasks"):
        op.create_table(
            "agent_tasks",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("book_id", sa.String(64), nullable=False, server_default="", index=True),
            sa.Column("scene_id", sa.String(64), nullable=False, server_default="", index=True),
            sa.Column("from_agent", sa.String(32), nullable=False, server_default="author"),
            sa.Column("to_agent", sa.String(32), nullable=False, server_default=""),
            sa.Column("kind", sa.String(24), nullable=False, server_default="rehearsal"),
            sa.Column("goal", sa.Text(), nullable=False, server_default=""),
            sa.Column("input_ref", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(24), nullable=False, server_default="submitted"),
            sa.Column("artifact_ref", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if not _has_table("agent_messages"):
        op.create_table(
            "agent_messages",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("task_id", sa.String(64), nullable=False, server_default="", index=True),
            sa.Column("role", sa.String(16), nullable=False, server_default="request"),
            sa.Column("from_agent", sa.String(32), nullable=False, server_default=""),
            sa.Column("to_agent", sa.String(32), nullable=False, server_default=""),
            sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    if _has_table("agent_messages"):
        op.drop_table("agent_messages")
    if _has_table("agent_tasks"):
        op.drop_table("agent_tasks")
