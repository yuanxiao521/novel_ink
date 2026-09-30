"""Belief ORM：信念账本（书级实体 · v1.5 独立落库）。

sim 运行时的 beliefs（char_id → [Belief]）在每回合落库后同步 upsert 到本表，
成为跨场景持续累积的书级认知账本；作者可在人物页查看 / 手改（edited=True）/ 删除。

- book_id：书级账本归属（FK books）
- char_id：宽松引用（不 FK characters，兼容场景特设/静态模板角色 id）
- fact_id：事实溯源（手改可为空）
- 稳定性：id 由 (book_id, char_id, fact_id) 哈希生成，sim 同步按 key 幂等覆盖
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book


class Belief(TimestampMixin, Base):
    __tablename__ = "beliefs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    char_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    fact_id: Mapped[str] = mapped_column(String(64), default="")
    source_event_id: Mapped[str] = mapped_column(String(64), default="")
    channel: Mapped[str] = mapped_column(String(20), default="perceived")  # perceived/told/inferred
    text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    edited: Mapped[bool] = mapped_column(default=False)  # 作者手改/编辑标记
    ts: Mapped[int] = mapped_column(Integer, default=0)  # 产生时间（毫秒）

    book: Mapped["Book"] = relationship(back_populates="beliefs")