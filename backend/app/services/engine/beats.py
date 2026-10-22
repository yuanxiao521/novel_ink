"""场级必然事件 beats（v1.54 · 0-token 判定）。

基调（作者拍板）：**主笔定"必然发生的事"，导演定"怎么走到那"，角色只决定反应。**
所以 beats 是**结构约束**，不是建议：
  hard  必然事件：不可取消、不可改成别的结论；未达成会被收束闸门拦下（最多再推 2 回合）
  soft  期望事件：只作为导演倾向，不校验

判定为什么用 0-token 关键词共现而不是问模型：
  ① 一次推演本来就贵，再让模型判"这条达成了吗"是重复花钱；
  ② 关键词判定可复现、可解释（能告诉作者"命中哪几个锚点"）；
  ③ 宁可漏报不假报：锚点命中不足就判未达成，让收束闸门再推一轮，而不是糊弄过去。
"""
from __future__ import annotations

from typing import Any, Iterable

LEVELS: tuple[str, ...] = ("hard", "soft")
LEVEL_LABEL = {"hard": "必然", "soft": "期望"}
MIN_ANCHOR_HITS = 2      # 有 >=2 个锚点时，命中 2 个才算达成


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


def parse_beats(raw: object) -> list[dict]:
    """把 plan_cfg_json.beats 归一成 [{id,text,level,anchors}]（坏数据跳过，不抛）。"""
    if not raw or not isinstance(raw, list):
        return []
    out: list[dict] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            text, level, anchors = _clean(item), "hard", []
        elif isinstance(item, dict):
            text = _clean(item.get("text") or item.get("event") or "")
            level = str(item.get("level") or item.get("kind") or "hard").strip().lower()
            level = level if level in LEVELS else "hard"
            anchors = [_clean(a) for a in (item.get("anchors") or []) if _clean(a)][:6]
        else:
            continue
        if not text:
            continue
        bid = str((item.get("id") if isinstance(item, dict) else "") or ("b" + str(i + 1)))
        out.append({"id": bid, "text": text, "level": level,
                    # 没给锚点就退化成"整条文本"当唯一锚点（诚实：判定会更严）
                    "anchors": anchors or [text]})
    return out


def hard_beats(beats: Iterable[dict]) -> list[dict]:
    return [b for b in beats or [] if b.get("level") == "hard"]


def beats_block(beats: list[dict], *, where: str = "本场") -> str:
    """压成导演 prompt 里的一段：hard 不可取消，soft 只作倾向。"""
    if not beats:
        return ""
    hard = [b for b in beats if b.get("level") == "hard"]
    soft = [b for b in beats if b.get("level") == "soft"]
    lines: list[str] = []
    if hard:
        lines.append("【" + where + "必然事件（不可取消：你可以决定怎么发生，但不能改成别的结论）】")
        lines += ["- " + str(b.get("text") or "") for b in hard]
    if soft:
        lines.append("【" + where + "期望事件（有则更好，不强制）】")
        lines += ["- " + str(b.get("text") or "") for b in soft]
    return "\n".join(lines)


def check_beats(beats: list[dict], texts: Iterable[str]) -> dict[str, Any]:
    """0-token 判定：只判 hard；返回达成/未达成与命中证据（可给作者看）。"""
    blob = " ".join(_clean(t) for t in texts or [])
    squeezed = blob.replace(" ", "")
    achieved: list[str] = []
    missed: list[str] = []
    evidence: dict[str, list[str]] = {}
    for b in hard_beats(beats):
        anchors = b.get("anchors") or [b.get("text") or ""]
        hits = [a for a in anchors
                if a and (_clean(a) in blob or _clean(a).replace(" ", "") in squeezed)]
        evidence[str(b["id"])] = hits
        need = min(MIN_ANCHOR_HITS, len(anchors)) or 1
        if len(hits) >= need:
            achieved.append(str(b["id"]))
        else:
            missed.append(str(b["id"]))
    return {"achieved": achieved, "missed": missed, "evidence": evidence,
            "judged": len(achieved) + len(missed),
            "texts": {str(b["id"]): str(b.get("text") or "") for b in hard_beats(beats)}}


def record_status(beats: list[dict], result: dict[str, Any]) -> dict[str, Any]:
    """把一次判定整理成可落库/可展示的状态（含未达成原文，便于提示作者）。"""
    texts = result.get("texts") or {}
    missed = list(result.get("missed") or [])
    return {
        "achieved": list(result.get("achieved") or []),
        "missed": missed,
        "missed_texts": [texts.get(i, "") for i in missed],
        "evidence": result.get("evidence") or {},
        "judged": int(result.get("judged") or 0),
        "ok": not missed,
    }

def apply_beats_gate(sim, beats: list[dict], *, max_retries: int = 2) -> dict | None:
    """收束闸门（v1.54）：hard beats 未达成时最多再推 max_retries 回合。

    返回本次判定状态（写进 sim.scratch["beats_status"]）；"没有 beats" 时返回 None。
    为什么单独成函数：这是"情节不能被自由发挥改掉"的唯一强制点，必须可单测——
    否则要么形同虚设（永远不拦），要么死循环（永远拦）。
    """
    beats = list(beats or [])
    if not beats:
        return None
    texts: list[str] = []
    for ev in getattr(sim, "events", []) or []:
        payload = getattr(ev, "payload", None) or {}
        if isinstance(payload, dict):
            texts += [str(v) for v in payload.values() if isinstance(v, str)]
        actor = getattr(ev, "actor", "")
        if actor:
            texts.append(str(actor))
    res = check_beats(beats, texts)
    status = record_status(beats, res)
    tries = int(getattr(sim, "scratch", {}).get("beats_retry") or 0)
    status["retries"] = tries
    if getattr(sim, "scratch", None) is not None:
        sim.scratch["beats_status"] = status
    if not status["missed"]:
        return status
    if tries >= max_retries:
        status["exhausted"] = True     # 补救次数用完 → 允许带缺陷收束（如实标记）
        return status
    sim.scratch["beats_retry"] = tries + 1
    sim.director.converged = False      # 拦下：继续推，直到达成或用完补救次数
    missing = "；".join(t for t in status.get("missed_texts") or [] if t)
    hint = str(getattr(sim.director.hint, "stage_prompt", "") or "")
    sim.director.hint.stage_prompt = (hint + " ｜必然事件还没发生：" + missing)[:400]
    return status

