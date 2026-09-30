"""Character ORM：角色卡（一等实体 · 含④层 prompt 可编辑字段 + pgvector 预留）。

v1.4 归属升级为「书级 + 场景特设」两层：
  - 书级角色：book_id 指向书，scene_id 为空 —— 全书所有场景自动引用，一次编辑生效；
  - 场景特设角色：book_id + scene_id 都有值 —— 仅在该场景登场（群演/NPC）。

spec_json 承载 CharacterCard 全字段（见 schemas/models.py），作者可在前端人物页编辑。
embedding 列为未来记忆 RAG 预留（pgvector），本次不参与计算。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.book import Book
    from app.db.models.scene import Scene


class Character(TimestampMixin, Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=False)
    scene_id: Mapped[Optional[str]] = mapped_column(ForeignKey("scenes.id"), index=True, nullable=True)  # 特设角色才有值
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, default="{}")  # CharacterCard 全字段
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)  # RAG 预留

    book: Mapped["Book"] = relationship(back_populates="characters")
    scene: Mapped[Optional["Scene"]] = relationship(back_populates="characters")