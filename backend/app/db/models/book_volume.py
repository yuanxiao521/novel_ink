"""BookVolume ORM：卷（v1.59 · 0023）。

卷是**戏剧单位**（起承转合/大高潮），不是算术切片：由主笔规划、作者可微调。

稳定性约定（重要）：
  - 本表 range_from/range_to 是「章属于哪一卷」的**唯一真源**；
  - chapters.volume_id 是**派生缓存**（只由 sync_volume_assignments 写），
    读路径发现不一致时以 range 为准自愈；
  - 卷边界变化不清空 book_digests，而是标 stale 等重算（不丢内容）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book


class BookVolume(TimestampMixin, Base):
    __tablename__ = "book_volumes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), index=True, nullable=False)
    no: Mapped[int] = mapped_column(Integer, default=1)          # 第几卷（1-based）
    title: Mapped[str] = mapped_column(String(120), default="")  # 卷名（主笔起，可改）
    range_from: Mapped[int] = mapped_column(Integer, default=1)  # 起始章号（闭区间）
    range_to: Mapped[int] = mapped_column(Integer, default=1)
    arc_goal: Mapped[str] = mapped_column(Text, default="")      # 本卷要完成什么
    climax: Mapped[str] = mapped_column(Text, default="")        # 本卷高潮/卷末转折

    book: Mapped["Book"] = relationship(back_populates="volumes")
