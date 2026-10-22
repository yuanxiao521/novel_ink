"""0025: prose_notes 支持章级审计（v1.65 · 两个页面合并）

为什么必须改表：v1.64 之前所有审校（体检/质检/润色/AI 味）都是**场级**的，落 prose_notes 时
`scene_id` 是 NOT NULL 外键 —— 所以章级审校（chapters.prose_md 为单位）根本落不了审计，
只能"跑完给个结果、刷新即失"。合并进章级工作台后，章级审校必须可追溯。

改法：
  - 新增 `chapter_id`（可空，随 chapters 级联删）+ 索引；
  - `scene_id` 改为可空（章级记录没有场 —— 直写章可以不拆场）；
  - 约束口径：一条记录要么挂场（场级审校），要么挂章（章级审校），至少挂一个。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_prose_note_chapter"
down_revision = "0024_chapter_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prose_notes", sa.Column(
        "chapter_id", sa.String(64),
        sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_prose_notes_chapter", "prose_notes", ["chapter_id"])
    op.alter_column("prose_notes", "scene_id",
                    existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    # 回退前先删掉"只挂章"的记录：否则 scene_id 的 NOT NULL 无法恢复
    op.execute("DELETE FROM prose_notes WHERE scene_id IS NULL")
    op.alter_column("prose_notes", "scene_id",
                    existing_type=sa.String(64), nullable=False)
    op.drop_index("ix_prose_notes_chapter", table_name="prose_notes")
    op.drop_column("prose_notes", "chapter_id")
