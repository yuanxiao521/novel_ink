"""0024: chapter_revisions —— 章正文版本表（v1.61）

产品问题：chapters.prose_md 是"当前版"，保存即覆盖 —— 作者改坏了想回上一版，做不到。
（B58 那次"一次 PUT 清空整场设定"就是这类风险的实例：幸好那是场景字段，正文没有版本兜底。）

设计（append-only 版本表）：
  - chapter_revisions(chapter_id, version, prose_md, source)，(chapter_id, version) 唯一；
  - 只追加、不更新、不删除（随 chapters 级联删）；
  - version = 该章当前 max(version) + 1（并发写同章的场景目前不存在：一章一个编辑者）；
  - source 标记来源：author（保存/恢复）/ restore（恢复动作本身也产生一条新版本，保证历史连续）。

恢复语义：恢复 = 把历史版本的正文写回 prose_md，并**追加一条新版本**（不是指针回拨）——
这样"恢复"本身也有历史可查，不会凭空消失。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_chapter_revisions"
down_revision = "0023_book_volumes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chapter_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("chapter_id", sa.String(64),
                  sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("prose_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="author"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_chapter_revisions_chapter", "chapter_revisions", ["chapter_id"])
    op.create_index("ix_chapter_revisions_chapter_version", "chapter_revisions",
                    ["chapter_id", "version"], unique=True)


def downgrade() -> None:
    op.drop_table("chapter_revisions")
