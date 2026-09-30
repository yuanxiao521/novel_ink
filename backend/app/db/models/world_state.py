"""WorldState ORM：世界状态账本（书级实体 · S1 优化）。

存储"写作用的世界状态骨架"：战力量级 / 金钱 / 道具链 / 时间锚 / 术语 / 关键数字。
比照 beliefs 的"宽引用 + 溯源 + 作者可编辑"结构：
  - key 为语义键（char:林尘:境界 / item:林尘:屠龙剑 / time:全书:当前日 / term:通用:灵石 / numeric:林尘:战力）
  - UNIQUE(book_id, kind, key) 幂等 upsert 依据
  - edited=False 视为未被作者手改的真实状态（感知注入用）
```
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book


class WorldState(TimestampMixin, Base):
    __tablename__ = "world_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # ws-{book_id|kind|key 哈希}
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # character/item/time/term/numeric
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 状态对象名
    key: Mapped[str] = mapped_column(String(200), nullable=False)   # 语义键（幂等 upsert 依据）
    value: Mapped[str] = mapped_column(Text, default="")            # 当前值
    scene_no: Mapped[int] = mapped_column(Integer, default=0)       # 记录于第几场景（时间序）
    source_event_id: Mapped[str] = mapped_column(String(64), default="")  # 溯源：PROSE_SAVE etc.
    previous_value: Mapped[str] = mapped_column(Text, default="")   # 上一个值（append 前值）
    edited: Mapped[bool] = mapped_column(Boolean, default=False)    # 作者手改标记
    ts: Mapped[int] = mapped_column(Integer, default=0)             # 产生时间（毫秒）

    book: Mapped[Optional["Book"]] = relationship(back_populates="world_states")

    __table_args__ = (
        Index("ux_world_states_book_kind_key", "book_id", "kind", "key", unique=True),
    )