"""Foreshadow ORM：伏笔账本（三态生命周期，Book 级）。

由主笔建骨架时预埋（buried），导演在场景收束分析时推进（in_progress）或回收（closed）。
status ∈ buried / in_progress / closed；expected_close_scene 用于"逾期回收"告警。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book


class Foreshadow(TimestampMixin, Base):
    __tablename__ = "foreshadows"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="plot")   # plot/character/theme/world
    text: Mapped[str] = mapped_column(Text, default="")             # 伏笔描述
    status: Mapped[str] = mapped_column(String(20), default="buried")  # buried/in_progress/closed
    buried_scene: Mapped[str] = mapped_column(String(64), default="")  # 埋设于哪场
    expected_close_scene: Mapped[str] = mapped_column(String(64), default="")  # 期望回收场
    related_char_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    book: Mapped["Book"] = relationship(back_populates="foreshadows")