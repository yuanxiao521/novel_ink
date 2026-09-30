"""BookMemory ORM：书级记忆上下文（主笔 Agent · 记忆域）。

按 book_id 隔离，多本书互不串味；topic 分类：
  direction（创作方向）/ setting（已定设定）/ constraint（写作约束·元技能产出）/
  history（修改历史）/ preference（作者偏好）
source 标注记忆来源：chief（主笔提炼）/ author（作者声明）/ audit（记账回写）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book


class BookMemory(TimestampMixin, Base):
    __tablename__ = "book_memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(20), default="direction")
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="chief")  # chief/author/audit
    ts: Mapped[int] = mapped_column(BigInteger, default=0)           # 毫秒时间戳（筛选最近 N 条）

    book: Mapped["Book"] = relationship(back_populates="memories")