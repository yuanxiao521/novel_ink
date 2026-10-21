"""MemoryChunk ORM：记忆检索块（v1.49 · 记忆检索底座 / RAG）。

产品问题：章成为最小写作单位后，写第 N 章的上下文只带「上一章结尾 600 字」，
第 10 章对前 9 章几乎失忆（人设漂移 / 伏笔断线）。可每章摘要、角色卡、世界状态、
伏笔账本**早就在库里**，只是从没被"按需检索"过。

做法（单表多 kind 的 RAG 底座）：
  - 一切可检索素材切成 chunk 落这张表：章摘要 / 场景 / 角色卡 / 世界状态 / 伏笔 / 信念 / 书级记忆
  - 关键词通道：zhparser 中文分词 + tsvector（配置名 zh，见 0019 迁移；无扩展时退回 simple）
  - 向量通道：embedding vector(1024) + HNSW（配了 NOVEL_EMBED_* 才写，否则留空 → 纯关键词）
  - 两路 RRF 融合（rag/fusion.py）；任何失败都降级为空 Recall，绝不阻断写作

为什么不用 agent_project 的 MCP 直接查：MCP 返回的是"答案"，这里要的是"片段"
（由写手 prompt 自己组织）；且跨进程会破坏写路径与事务一致性。见 docs/产品方向v2。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Computed, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from pgvector.sqlalchemy import Vector

from app.db.base import Base, TimestampMixin


class MemoryChunk(TimestampMixin, Base):
    __tablename__ = "mem_chunks"
    __table_args__ = (
        Index("ix_mem_chunks_book_kind", "book_id", "kind"),
        Index("ix_mem_chunks_book_chapter", "book_id", "chapter_no"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)   # chapter/scene/character/world/foreshadow/belief/memory
    ref_id: Mapped[str] = mapped_column(String(64), default="")     # 源实体 id（重建时按它整组替换）
    chapter_no: Mapped[int] = mapped_column(Integer, default=0)     # 时间序（0 = 非章级素材）
    title: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    ts: Mapped[int] = mapped_column(BigInteger, default=0)          # 源数据更新毫秒时间戳
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1024), nullable=True)
    # 生成列：写入时不用管 tsv，PG 用中文分词配置 zh 自动算（见 0019 迁移）。
    # 用「字面量配置名」而不是参数化配置：to_tsvector(regconfig, text) 才是 IMMUTABLE，
    # 生成列只接受 IMMUTABLE 表达式。
    tsv: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('zh', coalesce(title, '') || ' ' || text)", persisted=True),
        nullable=True,
    )
