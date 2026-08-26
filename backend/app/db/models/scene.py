"""Scene ORM：微场景（第三层 · 本次重构核心挂载点）。

内容承载：
  - scenario_def     场景模板名（如 betrayal_night），用于加载 decide_fn/converge_fn 回退
  - initial_facts_json 初始事实（Fact 列表 JSON）
  - plan_cfg_json      导演 PlanCfg JSON（secret_facts/expose_target/goal_pressure/...）
  - cursor_pos         创作光标（作者上次写到哪）
角色卡在独立 characters 表（scene_id 关联），作者可编辑。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.character import Character
    from app.db.models.chapter import Chapter


class Scene(TimestampMixin, Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario_def: Mapped[str] = mapped_column(String(64), default="betrayal_night")
    initial_facts_json: Mapped[str] = mapped_column(Text, default="[]")
    plan_cfg_json: Mapped[str] = mapped_column(Text, default="{}")
    cursor_pos: Mapped[int] = mapped_column(default=0)
    stage_desc: Mapped[str] = mapped_column(Text, default="")   # 舞台布置文本（主笔规划产物，v2 / 0002）
    scene_summary: Mapped[str] = mapped_column(Text, default="")  # 收束分析产出的场景摘要（v2 / 0002）
    final_prose: Mapped[str] = mapped_column(Text, default="")    # 作者手动定稿的整场正文（0004 · 正文落库 P0）

    chapter: Mapped["Chapter"] = relationship(back_populates="scenes")
    characters: Mapped[list["Character"]] = relationship(
        back_populates="scene", cascade="all, delete-orphan"
    )