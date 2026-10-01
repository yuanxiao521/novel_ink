"""0013: 对齐真实表结构与 ORM（schema drift 修复 · B18）

背景：0001 用 `Base.metadata.create_all` 建表、0006/0011 用 `op.create_table`（表已存在即跳过），
于是"旧结构已存在"的库上会出现「迁移跑过、结构没改」的静默漂移：
  - `beliefs`：仍是 sim 级旧结构（复合主键 sim_id/char_id/fact_id/source_event_id/ts），
    缺 id/book_id/edited/created_at/updated_at → `repo.list_beliefs` 恒抛 UndefinedColumn
    → `_query_or_mem` 静默落内存态（v1.5 信念账本在 DB 模式下实际失效）
  - `chat_histories`：缺 updated_at（ORM TimestampMixin 需要）
本迁移按**实际列是否存在**判断（幂等、可重复执行）：
  - beliefs 旧结构 → 重建为书级结构；旧行按 simulation.book_id 回填，id 用 App 同款算法
    `bel-{md5(book_id:char_id:fact_id)[:12]}`；无法归属到书的行丢弃（旧结构无价值）
  - chat_histories 缺列 → 补 updated_at

Revision ID: 0013_reconcile_schema_drift
Revises: 0012_world_states
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_reconcile_schema_drift"
down_revision: Union[str, Sequence[str], None] = "0012_world_states"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    # ---------------- beliefs：旧 sim 级结构 → 书级结构 ----------------
    if "beliefs" in tables:
        cols = _columns(bind, "beliefs")
        if "id" not in cols:
            op.execute("DROP TABLE IF EXISTS beliefs_legacy CASCADE")
            op.execute("ALTER TABLE beliefs RENAME TO beliefs_legacy")
            op.create_table(
                "beliefs",
                sa.Column("id", sa.String(length=64), nullable=False),
                sa.Column("book_id", sa.String(length=64), nullable=False),
                sa.Column("char_id", sa.String(length=64), nullable=False),
                sa.Column("fact_id", sa.String(length=64), nullable=False, server_default=""),
                sa.Column("source_event_id", sa.String(length=64), nullable=False, server_default=""),
                sa.Column("channel", sa.String(length=20), nullable=False, server_default="perceived"),
                sa.Column("text", sa.Text(), nullable=False, server_default=""),
                sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
                sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()),
                sa.Column("ts", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("created_at", sa.DateTime(timezone=True),
                          server_default=sa.text("now()"), nullable=False),
                sa.Column("updated_at", sa.DateTime(timezone=True),
                          server_default=sa.text("now()"), nullable=False),
                sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
                sa.PrimaryKeyConstraint("id"),
            )
            op.execute(
                """
                INSERT INTO beliefs (id, book_id, char_id, fact_id, source_event_id,
                                     channel, text, confidence, edited, ts)
                SELECT 'bel-' || substr(md5(s.book_id || ':' || l.char_id || ':' ||
                                            coalesce(l.fact_id, '')), 1, 12),
                       s.book_id, l.char_id, coalesce(l.fact_id, ''),
                       coalesce(l.source_event_id, ''), coalesce(l.channel, 'perceived'),
                       coalesce(l.text, ''), coalesce(l.confidence, 0.5), false, coalesce(l.ts, 0)
                FROM beliefs_legacy l
                JOIN simulation s ON s.id = l.sim_id
                WHERE s.book_id IS NOT NULL
                """
            )
            op.execute("DROP TABLE beliefs_legacy")
            op.create_index("ix_beliefs_book_id", "beliefs", ["book_id"])
            op.create_index("ix_beliefs_char_id", "beliefs", ["char_id"])
        else:
            if "edited" not in cols:
                op.add_column("beliefs", sa.Column(
                    "edited", sa.Boolean(), nullable=False, server_default=sa.false()))
            if "created_at" not in cols:
                op.add_column("beliefs", sa.Column(
                    "created_at", sa.DateTime(timezone=True),
                    server_default=sa.text("now()"), nullable=False))
            if "updated_at" not in cols:
                op.add_column("beliefs", sa.Column(
                    "updated_at", sa.DateTime(timezone=True),
                    server_default=sa.text("now()"), nullable=False))

    # ---------------- chat_histories：补 updated_at ----------------
    if "chat_histories" in tables:
        cols = _columns(bind, "chat_histories")
        if "updated_at" not in cols:
            op.add_column("chat_histories", sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.text("now()"), nullable=False))


def downgrade() -> None:
    """不做破坏性回退（旧 sim 级结构无价值）；仅移除本迁移补的 updated_at。"""
    bind = op.get_bind()
    if "chat_histories" in _tables(bind) and "updated_at" in _columns(bind, "chat_histories"):
        op.drop_column("chat_histories", "updated_at")
