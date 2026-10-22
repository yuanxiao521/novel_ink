"""Chapter ORM：章节（第二层）。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book
    from app.db.models.scene import Scene


class Chapter(TimestampMixin, Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    order_no: Mapped[int] = mapped_column(default=0)
    # ---- 主笔规划产物（v2 / 0002） ----
    tone: Mapped[str] = mapped_column(String(20), default="")              # 基调 action/suspense/warmth/serene
    tension_curve: Mapped[str] = mapped_column(Text, default="")           # 张力曲线（JSON 或 raise/hold/release）
    word_target: Mapped[int] = mapped_column(Integer, default=0)           # 本章字数目标
    # ---- 直写链路（v1.46 / 0018）：章是最小可写单位，场是可选细分 ----
    prose_md: Mapped[str] = mapped_column(Text, default="")                # 本章正文（权威存储）
    # ---- 章级制作单（v1.50 / 0020）：主笔裁决"这一章怎么生产" ----
    mode: Mapped[str] = mapped_column(String(16), default="draft")         # draft/emerge/hybrid
    production_json: Mapped[str] = mapped_column(Text, default="{}")       # must_rehearse/beats/hook/word_split
    # v1.58 前情概要（结构化 JSON：events/state_changes/knowledge/hook/open_questions…），
    # 由章后后台任务生成；写手 prompt 只吃结构化字段，人看 summary_md
    digest_json: Mapped[str] = mapped_column(Text, default="{}")
    # v1.59：所属卷（**派生缓存**：只由 sync_volume_assignments 写；真源是 book_volumes.range）
    volume_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    book: Mapped["Book"] = relationship(back_populates="chapters")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="chapter", cascade="all, delete-orphan")