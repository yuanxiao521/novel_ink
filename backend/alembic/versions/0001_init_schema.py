"""init: books/chapters/scenes/characters + simulation 四层重构（基线迁移）

Revision ID: 0001_init_schema
Revises:
Create Date: 2026-08-25

设计说明：
  - 旧架构（events/beliefs 表 + simulation 无多级 id）整体废弃，事件/信念并入
    state_json 快照，由本基线一次性收敛。
  - upgrade：DROP 旧表（IF EXISTS 幂等）→ Base.metadata.create_all 建全新 ORM schema。
    任何环境（空库/脏库/已有库）跑 upgrade head 结果一致。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_init_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """收敛旧 schema → 全新 ORM schema（幂等）。"""
    # 1) 废弃旧架构表（IF EXISTS：空库首次迁移不报错）
    for tbl in ("events", "beliefs", "simulation"):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")

    # 2) 建全部 ORM 表（books/chapters/scenes/characters/simulation）
    from app.db.base import Base

    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    """回滚：删除四层结构新增表（simulation 亦重建，故一并删除）。"""
    for tbl in ("characters", "scenes", "chapters", "books", "simulation"):
        op.drop_table(tbl)