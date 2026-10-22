"""0020: chapters.mode + production_json —— 主笔「章级制作单」（v1.50）

问题：现在「这一章走直写还是走涌现」**没有任何数据结构承载**——
  /write 只走直写、/director 只走场景推演，作者得自己记着"这章去哪个页面"。
这不是 UI 问题，是**结构决策没人做**：章怎么生产，本该由主笔在排骨架时裁决。

本迁移给章加两列（刻意拆成两列，不是全塞 JSON）：
  - mode：这一章的生产方式
      draft  = 直写（写手按字数目标一次写满）
      emerge = 涌现（先把场推演完，再渲染成章）
      hybrid = 混合（关键场先推演，其余直写）
    单独成列：它要被查询/路由/页头徽标直接用，不该藏在 JSON 里。
  - production_json：制作单细节（must_rehearse / beats / hook / word_split）
    形状由主笔填，执行层（正文协作）照单执行；作者可在预览里覆盖 mode。

默认值选 draft：存量章都是直写链路产出的，语义如实。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_chapter_production"
down_revision = "0019_memory_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chapters", sa.Column(
        "mode", sa.String(16), nullable=False, server_default="draft"))
    op.add_column("chapters", sa.Column(
        "production_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("chapters", "production_json")
    op.drop_column("chapters", "mode")
