"""Book ORM：小说（顶层实体）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.chapter import Chapter
    from app.db.models.foreshadow import Foreshadow
    from app.db.models.inspiration_card import InspirationCard


class Book(TimestampMixin, Base):
    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    genre: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="planned")  # writing/planned/draft
    cover_init: Mapped[str] = mapped_column(String(4), default="墨")  # 封面首字/符号
    chapter_count: Mapped[int] = mapped_column(default=0)
    # ---- 主笔规划产物（v2 / 0002） ----
    synopsis: Mapped[str] = mapped_column(Text, default="")          # 一句话总纲/创作方向
    worldview_json: Mapped[str] = mapped_column(Text, default="{}")  # 世界观文本（premise/rules_text/background）
    world_rules_json: Mapped[str] = mapped_column(Text, default="[]")  # 结构化 WorldRule[]（LLM 解析产物）

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    foreshadows: Mapped[list["Foreshadow"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    inspirations: Mapped[list["InspirationCard"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )