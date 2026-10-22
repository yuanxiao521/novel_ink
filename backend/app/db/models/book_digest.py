"""BookDigest ORM：卷级 / 全书级前情概要（v1.58 · 0022）。

为什么要独立表而不是塞 books JSON：
  - 一本书会有多个卷级摘要（每 8 章一卷）+ 1 个全书级摘要，需要按 range 查询；
  - 它们会被反复**增量重算**（新增一章只重算所在卷），独立行便于 upsert 与对比；
  - 未来可视化（"卷时间线"）需要按 level/range 排序读。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class BookDigest(TimestampMixin, Base):
    __tablename__ = "book_digests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)   # volume | book
    range_from: Mapped[int] = mapped_column(Integer, default=0)
    range_to: Mapped[int] = mapped_column(Integer, default=0)
    # v1.59：卷级概要挂到卷 id 上（range 保留，便于边界变更后核对）
    volume_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # v1.59：陈旧标记 —— 卷边界/章序变了只标 stale（不删内容），读取时提示"待重算"
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_json: Mapped[str] = mapped_column(Text, default="{}")
    summary_md: Mapped[str] = mapped_column(Text, default="")
