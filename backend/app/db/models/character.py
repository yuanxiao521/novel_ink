"""Character ORM：角色卡（一等实体 · 含④层 prompt 可编辑字段 + pgvector 预留）。

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
    from app.db.models.scene import Scene


class Character(TimestampMixin, Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, default="{}")  # CharacterCard 全字段
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)  # RAG 预留

    scene: Mapped["Scene"] = relationship(back_populates="characters")