"""0021: characters.role_tier —— 角色分层（v1.53）

基调（用户拍板 5 档）：涌现只演"能改变局面的人"。
  protagonist 主角 / major 关键配角 / antagonist 反派  → 进回合
  minor 次要 / extra 背景                              → **不进回合**，只作"在场背景"喂给写手

为什么加列而不是塞 spec_json：
  分层要被**选角逻辑查询、UI 徽标、骨架/体检规则直接用**，不该藏在 JSON 里。
默认值选 minor（不默认主角）：
  迁移时把谁当主角是**猜测**，宁可少演也别误把龙套当主角；存量书用「补档位」一次性补齐（一次 LLM）。

下游影响：导演台开场默认 cast = 主角+关键+反派（上限 5）；写手 prompt 多一块「在场背景角色」。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_character_role_tier"
down_revision = "0020_chapter_production"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("characters", sa.Column(
        "role_tier", sa.String(16), nullable=False, server_default="minor"))


def downgrade() -> None:
    op.drop_column("characters", "role_tier")
