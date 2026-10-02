"""ProseAnnotation ORM：段落级批注（P2 · 正文协作副驾）。

作者选中正文的一段/一句写下意见 → 落库成「可追踪的改稿任务」：
  status: open → handled（责编/工具处理并出 diff，作者采纳后关闭）/ dismissed（作者撤销）
为什么落库（不是"给 agent 看"就够了）：刷新不丢 · 状态可查 · 可批量 · 可归因
（对应 prose_notes 的"谁发起"）· 能做「批注热点」反哺主笔的约束表。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.scene import Scene


class ProseAnnotation(TimestampMixin, Base):
    __tablename__ = "prose_annotations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id"), index=True, nullable=False)
    para_index: Mapped[int] = mapped_column(Integer, default=0)      # 段落序号（1-based，前端 ¶n）
    quote: Mapped[str] = mapped_column(Text, default="")             # 引用原文（用于校验定位是否失效）
    note: Mapped[str] = mapped_column(Text, default="")             # 作者意见
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/handled/dismissed
    created_by: Mapped[str] = mapped_column(String(24), default="author")
    handled_by_note_id: Mapped[str] = mapped_column(String(64), default="")  # 处理它的 prose_note
    ts: Mapped[int] = mapped_column(BigInteger, default=0)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    scene: Mapped["Scene"] = relationship(back_populates="annotations")
