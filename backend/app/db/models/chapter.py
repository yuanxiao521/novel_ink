"""Chapter ORM：章节（第二层）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

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

    book: Mapped["Book"] = relationship(back_populates="chapters")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="chapter", cascade="all, delete-orphan")