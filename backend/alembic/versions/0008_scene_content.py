"""0008: scenes 增加 goal / content_desc（主笔 Agent 升级 · 场景内容）

阶段②「场景级部分修改」：场景补齐主笔规划内容——
goal（本场张力目标/结局条件，此前 commit 落库时被丢弃）+ content_desc（场景内容描述：
事件梗概/冲突点/环境细节，供正文协作与导演台参考）。

Revision ID: 0008_scene_content
Revises: 0007_book_memories
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_scene_content"
down_revision: Union[str, Sequence[str], None] = "0007_book_memories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scenes", sa.Column("goal", sa.Text(), nullable=False, server_default=""))
    op.add_column("scenes", sa.Column("content_desc", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("scenes", "content_desc")
    op.drop_column("scenes", "goal")