"""ProseNote ORM：正文审计记录（阶段④ 正文协作工作区 · 可追溯核心）。

多角色（writer/editor/polisher/verifier）每次体检/润色/质检写一条记录：
status(pending → approved/rejected)，作者审阅确认后才生效；
落库行为（final_prose / 伏笔推进 / 信念 / 因果）全部关联记录可回查。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.scene import Scene


class ProseNote(TimestampMixin, Base):
    __tablename__ = "prose_notes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="writer")      # writer/editor/polisher/verifier
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending/approved/rejected
    suggestion: Mapped[str] = mapped_column(Text, default="")            # 问题/建议/改动摘要（JSON 摘要，≤200 字/条）
    before: Mapped[str] = mapped_column(Text, default="")                # 改动前正文
    after: Mapped[str] = mapped_column(Text, default="")                 # 改动后正文（润色产出/体检建议稿）
    created_by: Mapped[str] = mapped_column(String(16), default="writer")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")  # 质检结构化明细（阶段⑤ 记账依据）
    ts: Mapped[int] = mapped_column(BigInteger, default=0)

    scene: Mapped["Scene"] = relationship(back_populates="notes")