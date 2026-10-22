"""0022: 前情压缩 —— chapters.digest_json + books.volume_size + book_digests（v1.58）

产品问题：写第 N 章只带"上一章结尾 600 字"，第 10 章对前 9 章几乎失忆；
RAG 检索又不保证关键状态一定在场。所以需要**分层、结构化、有预算**的前情概要。

设计（详见 docs/前情压缩设计.md）：
- 章级 digest 存在 chapters.digest_json（结构化：events/state_changes/knowledge/hook…）
- 卷级/全书级 digest 存在 book_digests（level=volume/book，range 表示覆盖章区间）
- 卷边界：默认每 8 章一卷（books.volume_size），**主笔/作者可微调**（books.volumes_json 覆盖）
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_story_digests"
down_revision = "0021_character_role_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ① 章级前情概要（结构化 JSON）
    op.add_column("chapters", sa.Column(
        "digest_json", sa.Text(), nullable=False, server_default="{}"))
    # ② 卷边界：默认 8 章一卷；volumes_json 非空时优先（主笔/作者微调）
    op.add_column("books", sa.Column(
        "volume_size", sa.Integer(), nullable=False, server_default="8"))
    op.add_column("books", sa.Column(
        "volumes_json", sa.Text(), nullable=False, server_default="[]"))

    # ③ 卷级 / 全书级前情概要
    op.create_table(
        "book_digests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("book_id", sa.String(64),
                  sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),      # volume | book
        sa.Column("range_from", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("range_to", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("digest_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("summary_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_book_digests_book_id", "book_digests", ["book_id"])
    op.create_index("ix_book_digests_book_level", "book_digests", ["book_id", "level"])


def downgrade() -> None:
    op.drop_table("book_digests")
    op.drop_column("books", "volumes_json")
    op.drop_column("books", "volume_size")
    op.drop_column("chapters", "digest_json")
