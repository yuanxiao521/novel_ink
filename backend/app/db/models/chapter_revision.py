"""ChapterRevision ORM：章正文版本（v1.61 · 0024，append-only）。

为什么单独一张表：chapters.prose_md 是"当前版"（保存即覆盖），
作者改坏想回上一版时没有任何兜底 —— 这张表就是那个兜底（稿本历史）。

约定：
  - 只追加，不更新；version = 该章 max(version)+1；
  - (chapter_id, version) 唯一（防并发写穿）；
  - 恢复动作 = 把历史正文写回 prose_md 并追加一条新版本（source="restore"），
    历史**连续不断**，不会出现"指针回拨导致历史消失"。
  - 只有 created_at（这表的"更新"没有意义）；不继承 TimestampMixin。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.chapter import Chapter


class ChapterRevision(Base):
    __tablename__ = "chapter_revisions"
    __table_args__ = (
        UniqueConstraint("chapter_id", "version", name="ux_chrev_chapter_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prose_md: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="author")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    chapter: Mapped["Chapter"] = relationship(back_populates="revisions")
