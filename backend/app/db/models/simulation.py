"""Simulation ORM：运行实例（第四层 · 持整份快照 state_json）。

book_id/chapter_id/scene_id 三级外键；state_json 存 SimulationState 完整序列化
（含角色卡拷贝、事件日志、世界状态、导演状态、last_main_actor）。
运行时每次 step 仅 load + save 本表，不回查 characters 表（N+1 防线）。
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Simulation(TimestampMixin, Base):
    __tablename__ = "simulation"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True, nullable=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"), index=True, nullable=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id"), index=True, nullable=True)
    scenario: Mapped[str] = mapped_column(String(64), default="betrayal_night")
    turn: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    ended: Mapped[bool] = mapped_column(Boolean, default=False)