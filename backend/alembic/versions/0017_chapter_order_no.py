"""0017: chapters.order_no 归一到 1-based（B38 修复）

根因：`POST /books/{id}/chapters` 不带 order_no 时，ORM 默认值 0 直接落库
→ 概览复盘时间线渲染「第 0 章」，导出目录/排序的口径也跟着偏
（实测：book-0938c02c 两章 order_no = 0 / 1）。
代码侧已在 `service.create_chapter` 补齐「现有最大章号 + 1」，本迁移把**存量**归位：
按 book 分组、按 (order_no, created_at, id) 重排成 1..N（幂等：已经正确的行不会被改动）。

注意：这是一次**数据规整**，不是结构变更——没有 DDL，回滚是无操作（旧值不可恢复，
但旧值本身是错的，故 `downgrade` 只留说明）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_chapter_order_no"
down_revision = "0016_ts_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY book_id
                       ORDER BY order_no, created_at, id
                   ) AS rn
            FROM chapters
        )
        UPDATE chapters c
        SET order_no = r.rn
        FROM ranked r
        WHERE c.id = r.id AND c.order_no IS DISTINCT FROM r.rn
    """))


def downgrade() -> None:
    """数据规整不可逆（旧值本身就是错的）；升级前值可由备份恢复。"""
    pass
