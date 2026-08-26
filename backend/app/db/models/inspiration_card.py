"""InspirationCard ORM：灵感卡（Book 级，主笔共创工作台左栏"灵感池"）。

由主笔 LLM 生成（source=chief）或作者自建（source=author）；
adopted 采纳状态跨会话持久化，供主笔规划（plan_skelly）作为"已在酝酿的设定"引用。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book


class InspirationCard(TimestampMixin, Base):
    __tablename__ = "inspiration_cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    icon: Mapped[str] = mapped_column(String(16), default="✦")
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    desc: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(20), default="plot")  # plot/character/world
    source: Mapped[str] = mapped_column(String(16), default="chief")  # chief/author
    adopted: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped["Book"] = relationship(back_populates="inspirations")