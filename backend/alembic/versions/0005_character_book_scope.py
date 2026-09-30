"""0005: 角色卡归属升级为「书级 + 场景特设」（书级角色库 v1.4）

- characters + book_id（书级归属，NOT NULL 最终态）
- 旧数据 backfill：各角色经 scene → chapter → book 反查补 book_id；
  数据集全部视为书级（scene_id 置空），届时全书角色共享一份，编辑不再按场景重复。

Revision ID: 0005_character_book_scope
Revises: 0004_scene_final_prose
Create Date: 2026-08-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_character_book_scope"
down_revision: Union[str, Sequence[str], None] = "0004_scene_final_prose"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 先加可空 book_id
    op.add_column("characters", sa.Column("book_id", sa.String(length=64), nullable=True))

    # 2) backfill：scene → chapter → book 反查
    op.execute(
        """
        UPDATE characters AS c
        SET book_id = ch.book_id
        FROM scenes AS s
        JOIN chapters AS ch ON ch.id = s.chapter_id
        WHERE c.scene_id = s.id
          AND c.book_id IS NULL
        """
    )

    # 2.5) scene_id 改为可空（书级角色不挂场景；特设角色才填）
    op.alter_column("characters", "scene_id", existing_type=sa.String(length=64), nullable=True)

    # 3) 孤儿（无场景）角色直接删除，避免 NOT NULL 冲突
    op.execute("DELETE FROM characters WHERE book_id IS NULL")

    # 4) 全部角色归书级（scene_id 置空）：编辑不随场景分叉
    op.execute("UPDATE characters SET scene_id = NULL")

    # 5) 加不可空约束 + 索引
    op.alter_column("characters", "book_id", existing_type=sa.String(64), nullable=False)
    op.create_index("ix_characters_book_id", "characters", ["book_id"])


def downgrade() -> None:
    op.drop_index("ix_characters_book_id", table_name="characters")
    op.drop_column("characters", "book_id")