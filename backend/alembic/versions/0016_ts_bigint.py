"""0016: beliefs.ts / world_states.ts 由 integer 改为 bigint（B28 修复）

根因：这两列是 int32，但写入端用 `int(time.time() * 1000)`（毫秒，~1.79e12 > 2^31）
→ asyncpg DataError「value out of int32 range」→ `_upsert` 捕获后静默降级内存态
→ 作者手写信念/世界状态在 DB 模式**从未真正落库**（库里 beliefs.ts 只有 ts=0 的行）。
其余各表（book_memories/prose_notes/chat_histories/agent_tasks/…）的 ts 本就是 bigint。

幂等：按 information_schema 实际 data_type 判断，已是 bigint 则跳过。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_ts_bigint"
down_revision = "0015_agent_tasks"
branch_labels = None
depends_on = None


def _col_type(table: str, column: str) -> str | None:
    bind = op.get_bind()
    row = bind.execute(sa.text(
        "select data_type from information_schema.columns "
        "where table_name = :t and column_name = :c"
    ), {"t": table, "c": column}).first()
    return row[0] if row else None


def upgrade() -> None:
    for table in ("beliefs", "world_states"):
        if _col_type(table, "ts") == "integer":
            op.alter_column(table, "ts",
                            existing_type=sa.Integer(),
                            type_=sa.BigInteger(),
                            existing_nullable=False,
                            existing_server_default="0")


def downgrade() -> None:
    for table in ("beliefs", "world_states"):
        if _col_type(table, "ts") == "bigint":
            op.alter_column(table, "ts",
                            existing_type=sa.BigInteger(),
                            type_=sa.Integer(),
                            existing_nullable=False,
                            existing_server_default="0")
