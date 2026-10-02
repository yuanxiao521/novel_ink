"""AgentTask / AgentMessage ORM：任务总线（L3 消息层 · A+ 批）。

设计依据：docs/正文协作副驾与Agent协作架构.md §5/§8.2。
- **只传请求/裁决/通知，不传状态**（状态走黑板或账本，防第二套事实来源）
- 状态机：submitted → working → input-required（等作者确认）→ completed / failed
- 发起者硬边界：只有 作者 / 编排者 / 主笔 / 责编 能发起；**角色 agent 不允许发起任务**
"""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentTask(TimestampMixin, Base):
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    scene_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    from_agent: Mapped[str] = mapped_column(String(32), default="author")
    to_agent: Mapped[str] = mapped_column(String(32), default="")
    kind: Mapped[str] = mapped_column(String(24), default="rehearsal")   # rehearsal/expose/adjudicate/rewrite
    goal: Mapped[str] = mapped_column(Text, default="")
    input_ref: Mapped[str] = mapped_column(Text, default="{}")           # 输入引用（JSON，不放正文）
    status: Mapped[str] = mapped_column(String(24), default="submitted")  # submitted/working/input-required/completed/failed
    artifact_ref: Mapped[str] = mapped_column(Text, default="{}")         # 产物引用（sim_id / note_id / scene_id）
    ts: Mapped[int] = mapped_column(BigInteger, default=0)


class AgentMessage(TimestampMixin, Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    role: Mapped[str] = mapped_column(String(16), default="request")      # request/response/notify
    from_agent: Mapped[str] = mapped_column(String(32), default="")
    to_agent: Mapped[str] = mapped_column(String(32), default="")
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    ts: Mapped[int] = mapped_column(BigInteger, default=0)
