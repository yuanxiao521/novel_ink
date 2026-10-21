"""0018: chapters.prose_md —— 直写链路（章成为最小可写单位）

背景（v1.46 · 产品方向 v2）：
  原来「场」是唯一可写单位（正文只存在 scenes.final_prose），所以**没有场景就写不了小说**，
  而一场正文只有 150-220 字（写手 prompt 写死）→ 全书至今 286 字。
  方向校准后定为：**章是最小可写单位，场是可选的细分**。

本迁移给 chapters 加章正文列（权威存储）：
  - prose_md：本章正文（Markdown 纯文本，含段落换行）
  - 字数不落列：由 text_metrics.prose_chars 现算（B39：字数口径必须唯一）

有场时：场正文仍是推演/细分的产物，章正文可由"场拼接"或"直写"产生；两种都存 prose_md，
避免出现第二个字数/正文真源。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_chapter_prose"
down_revision = "0017_chapter_order_no"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chapters", sa.Column("prose_md", sa.Text(), nullable=False,
                                        server_default=sa.text("''")))


def downgrade() -> None:
    op.drop_column("chapters", "prose_md")
