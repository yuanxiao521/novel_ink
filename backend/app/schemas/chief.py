"""Schema 层 · 主笔共创（chief_studio）接口出入模型。

对齐前端 frontend/src/api/novel.ts 的 InspirationCard TS 类型：
id / book_id / icon / title / desc / type / source / adopted / sort_order —— 一一对应。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class InspirationIn(BaseModel):
    """作者自建灵感卡（POST /books/{id}/inspirations）。"""

    title: str = Field(..., max_length=64)
    desc: str = ""
    icon: str = "✦"
    type: str = "plot"          # plot / character / world


class InspirationOut(BaseModel):
    """灵感卡出参（单卡全字段，与前端 TS 对齐）。"""

    id: str
    book_id: str
    icon: str = "✦"
    title: str
    desc: str = ""
    type: str = "plot"
    source: str = "chief"       # chief / author
    adopted: bool = False
    sort_order: int = 0


class GenerateIn(BaseModel):
    """主笔生成灵感卡（POST /books/{id}/inspirations/generate）。"""

    direction: str = ""


class AdoptBody(BaseModel):
    """采纳⇄取消（PATCH /inspirations/{id}）。"""

    adopted: bool


class ChatMessage(BaseModel):
    """主笔共创对话单条消息（POST /books/{id}/chief/chat）。"""

    role: str = "user"          # user / assistant
    content: str = ""


class ChiefChatIn(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    max_tokens: Optional[int] = None


class MemoryIn(BaseModel):
    """书级记忆（POST /books/{id}/memories）。

    topic ∈ direction|setting|constraint|history|preference；
    source ∈ chief|author|audit（默认 author，非法值由服务层兜底为 author）。
    """

    topic: str = "direction"
    content: str = Field(..., max_length=2000)
    source: str = "author"


class MemoryUpdateIn(BaseModel):
    """编辑记忆（PUT /memories/{id}）：字段全可选，只更新显式传入的。"""

    topic: str = ""
    content: str = ""
    source: str = ""