"""0019: mem_chunks —— 记忆检索底座（RAG · v1.49）

产品问题：章成为最小写作单位后，写第 N 章的上下文只带「上一章结尾 600 字」，
第 10 章对前 9 章几乎失忆（人设漂移 / 伏笔断线）。而每章摘要、角色卡、世界状态、
伏笔账本早就在库里，只是从没被"按需检索"过。

本迁移做三件事：
  1. 中文分词配置 zh：优先 zhparser（SCWS 分词，人名/术语切得开），
     装不上就 COPY = simple 退化——**名字固定为 zh**，代码侧只认这一个配置名，
     不会因为某台机器缺扩展就整个检索功能消失。
  2. mem_chunks 表：单表多 kind（chapter/scene/character/world/foreshadow/belief/memory）。
  3. 三类索引：
       - (book_id, kind) / (book_id, chapter_no) 普通 B-tree（过滤）
       - tsv 的 GIN（关键词检索）
       - embedding 的 HNSW cosine（向量通道 · 配了 NOVEL_EMBED_* 才会写入）

tsv 是 **生成列**（GENERATED ALWAYS AS ... STORED）：写入方不用维护，
表达式用字面量配置名 'zh' 才是 IMMUTABLE（生成列要求）。

降级诚实说明：没有 zhparser 时 simple 分词对中文几乎只能整段匹配，
关键词检索效果会明显变差（不报错、结果变少）。/health.rag 会显示分词配置实际用的是哪个。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0019_memory_rag"
down_revision = "0018_chapter_prose"
branch_labels = None
depends_on = None

TS_CONFIG = "zh"


_EXT_SQL = """
DO $$
BEGIN
    BEGIN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector 不可用：%', SQLERRM;
    END;

    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'zhparser') THEN
        BEGIN
            EXECUTE 'CREATE EXTENSION zhparser';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'zhparser 不可用，中文分词退化为 simple：%', SQLERRM;
        END;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'zh') THEN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'zhparser') THEN
            EXECUTE 'CREATE TEXT SEARCH CONFIGURATION zh (PARSER = zhparser)';
        ELSE
            EXECUTE 'CREATE TEXT SEARCH CONFIGURATION zh (COPY = simple)';
        END IF;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_ts_config_map m
        JOIN pg_ts_config c ON c.oid = m.mapcfg
        WHERE c.cfgname = 'zh'
    ) THEN
        BEGIN
            EXECUTE 'ALTER TEXT SEARCH CONFIGURATION zh ADD MAPPING FOR n,v,a,i,e,l WITH simple';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'zh 分词映射添加失败（保持无映射）：%', SQLERRM;
        END;
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(_EXT_SQL)

    op.create_table(
        "mem_chunks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("book_id", sa.String(64),
                  sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("ref_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("chapter_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("meta_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column(
            "tsv", TSVECTOR,
            sa.Computed("to_tsvector('zh', coalesce(title, '') || ' ' || text)", persisted=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_index("ix_mem_chunks_book_id", "mem_chunks", ["book_id"])
    op.create_index("ix_mem_chunks_book_kind", "mem_chunks", ["book_id", "kind"])
    op.create_index("ix_mem_chunks_book_chapter", "mem_chunks", ["book_id", "chapter_no"])
    op.create_index("ix_mem_chunks_tsv", "mem_chunks", ["tsv"], postgresql_using="gin")
    # HNSW：pgvector 0.5+ 支持；空表建索引是秒级，之后写入增量维护
    op.execute(
        "CREATE INDEX ix_mem_chunks_embedding_hnsw ON mem_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("mem_chunks")
