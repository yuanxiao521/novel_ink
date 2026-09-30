"""ChatHistory ORM：主笔共创对话历史（阶段⑧ 对话持久化 · 按书隔离）。

每条消息记录 role（user/assistant）、content、时间戳。
前端切换书时加载该书历史，新建书从空白开始。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book


class ChatHistory(TimestampMixin, Base):
    __tablename__ = "chat_histories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user")  # user/assistant
    content: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[int] = mapped_column(BigInteger, default=0)  # 毫秒时间戳（排序用）

    book: Mapped["Book"] = relationship(back_populates="chat_histories")
