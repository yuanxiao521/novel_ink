"""对话快速通道（v1.73 · 方案三）：纯对话过场一拍 = 1 次调用生成整段交锋。

什么时候用：导演 pace.mode=volley（无硬节拍、无冲突的过场），在场 ≤3 人。
为什么便宜又快：一次 LLM 调用出在场角色各 1-2 句台词的完整交锋（剧本体），
替代 3×(思考+决策) 的 6 次调用。**护栏不省**：逐行 persona/world guard（拆行后
复用与逐角色 decide 完全同一套判据），拦截行丢弃（该行不落库），不整段重写。

诚实边界：这不是"各自独立思考后的涌现"（每个角色不是单独过一遍 perceive→think
→decide），是模型一次写完的合演 —— 所以只给过场用，硬节拍/冲突永远走主通道。
"""
from __future__ import annotations

from typing import Any, Optional

from app.schemas import models


VOLLEY_SCHEMA = (
    '{"lines":[{"actor":"角色id","kind":"dialogue|action","text":"一句话台词或行动"}],'
    '"summary":"这一段交锋一句话摘要"}'
)


def volley_prompt(sim: models.SimulationState, order: list[str]) -> str:
    """在场角色互相交锋的生成提示（角色人设注入，导演舞台提示只作倾向）。"""
    names = {}
    for cid in order:
        card = sim.characters.get(cid)
        if card is None:
            continue
        bottom = "；".join(card.bottom_lines) if card.bottom_lines else "无"
        goals = "；".join(g.text for g in card.dynamic_goals) or "—"
        names[cid] = {"name": card.name, "summary": card.summary, "goals": goals, "bottom": bottom}

    rows = "\n".join(
        f"- {cid}（{v['name']}）：{v['summary']}｜目标：{v['goals']}｜底线（绝不越过）：{v['bottom']}"
        for cid, v in names.items()
    )
    facts = "；".join(f.text for f in sim.world.facts if f.active)[:400]
    stage = (sim.world.env_conds or [""])[0] if sim.world.env_conds else ""
    hint = str(getattr(sim.director.hint, "stage_prompt", "") or "")
    recent = [e for e in sim.events[-6:] if getattr(e, "source", None) and e.source.value == "character"]
    recent_txt = "；".join(f"{e.actor}：{e.payload.get('text','')}" for e in recent) or "（刚开场）"

    return (
        "你在为小说场景写一段**过场交锋**（2-4 个来回的对话/短动作）。\n"
        "要求：\n"
        "· 每行一句话（台词用第一人称原话；动作用第三人称短句）；\n"
        "· 严格贴合各角色人设/目标/底线，台词间要有真实的相互回应（不是各说各的）；\n"
        "· 不得越出【已知事实】，不得替角色做重大决定；\n"
        f"· 总计 {len(order) * 2} 行左右，最多 {len(order) * 3} 行。\n\n"
        f"【在场角色】\n{rows}\n\n"
        f"【舞台】{stage or '（无）'}\n"
        f"【导演提示（只作倾向）】{hint or '（无）'}\n"
        f"【眼前事实】{facts or '（无）'}\n"
        f"【近况】{recent_txt}\n\n"
        "输出 JSON：lines（每行 {actor, kind, text}）+ summary。"
    )


def parse_volley(raw: Any, sim: models.SimulationState, order: list[str]) -> list[dict]:
    """模型返回 → 行为列表（逐行清洗）。非法行丢弃：actor 不在场 / 空文本 / kind 越界。"""
    if not isinstance(raw, dict):
        return []
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return []
    allowed = {str(c) for c in order}
    out: list[dict] = []
    for ln in lines[: 12]:
        if not isinstance(ln, dict):
            continue
        actor = str(ln.get("actor") or "").strip()
        text = str(ln.get("text") or "").strip()
        kind = str(ln.get("kind") or "dialogue").strip()
        if actor not in allowed or not text or actor not in sim.characters:
            continue
        if kind not in ("dialogue", "action", "conflict"):
            kind = "dialogue"
        out.append({"actor": actor, "kind": kind, "text": text[:200], "new_fact": ""})
    return out


def volley_summary(raw: Any, fallback: str = "") -> str:
    if isinstance(raw, dict):
        return str(raw.get("summary") or "").strip()[:40] or fallback
    return fallback
