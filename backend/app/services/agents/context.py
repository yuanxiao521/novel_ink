"""上下文装配器（B 批）：**同一份 PerceptPacket，不同 persona 渲染成不同 prompt**。

意义：新增 agent 不再各写一套字符串拼装；"它到底看到了什么"由感知包负责，
"该怎么对它说话"由 persona 模板负责——两者解耦、可分别测试。
"""
from __future__ import annotations

import json

from app.services.agents.perceive import block

PERSONA_HEAD = {
    "editor": "你是这本书的**责编**，只负责**这一场的文字**：正文、口吻、事实一致、伏笔落地。不改章节骨架与世界观约束（那是主笔的活）。",
    "chief": "你是这本书的**主笔（总编）**，掌管**结构与约束**：书树、节奏、伏笔、角色弧，并维护书级约束表。你**不直接改正文**（那是责编的活）。",
}


def _fmt_items(items: list) -> str:
    return json.dumps(items, ensure_ascii=False)


def render(packet: dict, persona: str) -> str:
    head = PERSONA_HEAD.get(persona)
    if not head:
        raise ValueError(f"未知 persona：{persona}（可选 {sorted(PERSONA_HEAD)}）")
    parts = [head]

    scene = block(packet, "scene")
    if scene.get("items"):
        parts.append("【本场】" + _fmt_items(scene["items"]))
    book = block(packet, "book")
    if book.get("items"):
        parts.append("【本书】" + _fmt_items(book["items"]))
    cons = block(packet, "constraints")
    if cons.get("items"):
        c = cons["items"][0]
        parts.append(
            "【书级约束 · 全员遵守】\n"
            f"- 方向：{c.get('direction') or '（未填写）'}\n"
            f"- 世界观前提：{c.get('worldview_premise') or '（未填写）'}\n"
            f"- 硬规则：{'；'.join(c.get('hard_rules') or []) or '（无）'}\n"
            f"- 写作约束：{'；'.join(c.get('constraints') or []) or '（无）'}"
        )
    outline = block(packet, "outline")
    if outline.get("items"):
        parts.append("【骨架概览】" + _fmt_items(outline["items"]))
    chars = block(packet, "characters")
    if chars.get("items"):
        parts.append("【上场角色】" + "；".join(
            f"{c['name']}（{c.get('voice') or c.get('summary') or '—'}）" for c in chars["items"]
        ) or "未配置")
    mems = block(packet, "memories")
    if mems.get("items"):
        parts.append("【书级记忆】" + _fmt_items(mems["items"]))
    anns = block(packet, "annotations")
    parts.append("【待处理批注】" + (_fmt_items(anns["items"]) if anns.get("items") else "无"))
    notes = block(packet, "recent_notes")
    if notes.get("items"):
        parts.append("【最近审计】" + "；".join(f"{n['kind']}/{n['status']}: {n['suggestion']}" for n in notes["items"]))
    sig = block(packet, "writing_signals")
    if sig.get("items"):
        parts.append("【写作侧信号】" + _fmt_items(sig["items"]))
    draft = block(packet, "draft")
    if draft.get("items"):
        parts.append("【当前正文】" + (draft["items"][0].get("text") or "（空）"))
    if packet.get("dropped"):
        parts.append("【感知裁剪留痕】" + _fmt_items(packet["dropped"]))
    return "\n\n".join(parts)
