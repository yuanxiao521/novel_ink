"""S4 涌现产物渲染（剧本/对话记录）：纯函数 · 无 LLM · 无 IO。

产物形态（《写作优化方案-S4涌现式嵌入.md》§3.2）：
  · Markdown 剧本体：〔回合 N · 冲突〕+ 摘要 + 角色：台词 /（角色 动作）
    + 可选 〔内心独白·角色〕〔动机·角色〕 + 可选张力标注
  · JSON 结构化：同一份数据的机器形态（供桥接/二次加工）

数据来源：TurnArchive（含本回合 events=角色行动/台词 与 thoughts=各角色思考）。
"""
from __future__ import annotations

CLS_LABEL = {
    "type-conflict": "冲突",
    "type-dialogue": "对话",
    "type-action": "行动",
    "type-info": "信息",
}


def _turn_lines(archive: dict, with_thoughts: bool, with_tension: bool) -> list[str]:
    turn = archive.get("turn")
    cls = CLS_LABEL.get(str(archive.get("cls") or ""), "信息")
    head = "〔回合 %s · %s〕" % (turn, cls)
    if with_tension:
        head += "  ［张力 %s %s］" % (archive.get("tension"), archive.get("tension_trend") or "")
    lines = [head]
    summary = str(archive.get("summary") or "").strip()
    if summary:
        lines.append("· %s" % summary)
    for ev in archive.get("events") or []:
        actor = str(ev.get("actor") or "")
        kind = str(ev.get("kind") or "action")
        text = str(ev.get("text") or "").strip()
        if not text:
            continue
        lines.append("%s：%s" % (actor, text) if kind == "dialogue" else "（%s %s）" % (actor, text))
    if with_thoughts:
        for th in archive.get("thoughts") or []:
            char = str(th.get("char") or "")
            mono = str(th.get("monologue") or "").strip()
            why = str(th.get("reasoning") or "").strip()
            if mono:
                lines.append("〔内心独白·%s〕%s" % (char, mono))
            if why:
                lines.append("〔动机·%s〕%s" % (char, why))
    return lines


def render_script_md(scene: dict, archives: list[dict], *,
                     with_thoughts: bool = True, with_tension: bool = False) -> str:
    """渲染单场景剧本（Markdown 剧本体）。"""
    lines = ["# 剧本 · %s" % (scene.get("title") or "（未命名场景）"), ""]
    stage = str(scene.get("stage_desc") or "").strip()
    goal = str(scene.get("goal") or "").strip()
    if stage:
        lines.append("【场景】%s" % stage)
    if goal:
        lines.append("【本场目标】%s" % goal)
    ordered = sorted(archives or [], key=lambda a: a.get("turn") or 0)
    if not ordered:
        lines.append("")
        lines.append("（本场景尚无推演回合）")
        return "\n".join(lines)
    for a in ordered:
        lines.append("")
        lines.extend(_turn_lines(a, with_thoughts, with_tension))
    return "\n".join(lines)


def render_script_json(scene: dict, archives: list[dict]) -> dict:
    """渲染单场景剧本（结构化形态，供桥接/二次加工）。"""
    return {
        "scene": {"id": scene.get("id"), "title": scene.get("title"),
                  "stage_desc": scene.get("stage_desc"), "goal": scene.get("goal"),
                  "content_desc": scene.get("content_desc")},
        "turns": [
            {"turn": a.get("turn"), "cls": a.get("cls"), "summary": a.get("summary"),
             "tension": a.get("tension"), "tension_trend": a.get("tension_trend"),
             "events": a.get("events") or [], "thoughts": a.get("thoughts") or []}
            for a in sorted(archives or [], key=lambda a: a.get("turn") or 0)
        ],
    }
