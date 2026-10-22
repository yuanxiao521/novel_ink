"""0023: 卷实体化 —— book_volumes 表 + chapters.volume_id + book_digests 陈旧标记（v1.59）

为什么从"JSON 列"升级成"真表"（作者拍板）：
  卷不是算术切片（每 8 章一刀），而是**戏剧单位**，要由主笔规划、可微调、可查询。

稳定性的三个设计（这是本迁移的重点）：
  ① **结构唯一真源** = book_volumes 的 range_from/range_to；
     volumes_json / volume_size 仅作**回退**（老书无卷表时仍按 8 章均分），不做双写。
  ② chapters.volume_id 是**派生缓存**（可空），只由 sync_volume_assignments() 写；
     读路径发现与 range 不一致 → 以 range 为准自愈（避免"两套真相"导致查询分叉）。
  ③ book_digests 增 stale 标记：卷边界/章序一变，旧卷概要不删（不丢内容），
     标 stale=true 并在读取时提示"待重算"；重算后自动清除。

连锁影响（迁移同时处理）：
  - 章增删/重排（create/delete/commit_book_plan）→ 必须重算归属；
  - 卷级/全书级前情概要的 range 与 volume_id 关联；
  - 旧数据回填：有 volumes_json 用它，否则按 volume_size 切。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_book_volumes"
down_revision = "0022_story_digests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ① 卷表（结构唯一真源）
    op.create_table(
        "book_volumes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("book_id", sa.String(64),
                  sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(120), nullable=False, server_default=""),
        sa.Column("range_from", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("range_to", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("arc_goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("climax", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_book_volumes_book_id", "book_volumes", ["book_id"])
    op.create_index("ix_book_volumes_book_no", "book_volumes", ["book_id", "no"])

    # ② 章的卷归属（派生缓存，可空：老书/未分配）
    op.add_column("chapters", sa.Column("volume_id", sa.String(64), nullable=True))
    op.create_index("ix_chapters_volume_id", "chapters", ["volume_id"])

    # ③ 卷级概要：关联卷 id + 陈旧标记（不删内容，只标待重算）
    op.add_column("book_digests", sa.Column("volume_id", sa.String(64), nullable=True))
    op.add_column("book_digests", sa.Column(
        "stale", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # ④ 旧数据回填：有 volumes_json 用它，否则按 volume_size 切；再同步章节归属
    op.execute("""
DO $$
DECLARE
    b RECORD;
    v JSONB;
    idx INT;
    frm INT;
    t_to INT;
    total INT;
    size INT;
    vid TEXT;
BEGIN
    FOR b IN SELECT id, volume_size, volumes_json FROM books LOOP
        SELECT count(*) INTO total FROM chapters WHERE book_id = b.id;
        CONTINUE WHEN total = 0;
        size := GREATEST(1, COALESCE(b.volume_size, 8));
        idx := 0;
        BEGIN
            v := (b.volumes_json)::jsonb;
        EXCEPTION WHEN OTHERS THEN
            v := '[]'::jsonb;
        END;
        IF v IS NOT NULL AND jsonb_array_length(v) > 0 THEN
            FOR idx IN 0..jsonb_array_length(v) - 1 LOOP
                frm := (v -> idx ->> 'from')::int;
                t_to := (v -> idx ->> 'to')::int;
                CONTINUE WHEN frm IS NULL OR t_to IS NULL OR t_to < frm;
                vid := 'vol-' || b.id || '-' || idx + 1;
                INSERT INTO book_volumes (id, book_id, no, title, range_from, range_to)
                VALUES (vid, b.id, idx + 1, '', frm, t_to)
                ON CONFLICT (id) DO NOTHING;
            END LOOP;
        ELSE
            frm := 1;
            idx := 1;
            WHILE frm <= total LOOP
                t_to := LEAST(total, frm + size - 1);
                vid := 'vol-' || b.id || '-' || idx;
                INSERT INTO book_volumes (id, book_id, no, title, range_from, range_to)
                VALUES (vid, b.id, idx, '', frm, t_to)
                ON CONFLICT (id) DO NOTHING;
                frm := t_to + 1;
                idx := idx + 1;
            END LOOP;
        END IF;
    END LOOP;

    -- 回填章节归属（按 range 匹配；对不上就留空，后续 sync 会自愈）
    UPDATE chapters c SET volume_id = v.id
    FROM book_volumes v
    WHERE c.book_id = v.book_id
      AND c.order_no BETWEEN v.range_from AND v.range_to
      AND c.order_no > 0;
END
$$;
""")


def downgrade() -> None:
    op.drop_column("book_digests", "stale")
    op.drop_column("book_digests", "volume_id")
    op.drop_index("ix_chapters_volume_id", table_name="chapters")
    op.drop_column("chapters", "volume_id")
    op.drop_table("book_volumes")
