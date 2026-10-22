"""前情压缩（v1.58）：把正文压成**结构化前情概要**，按层注入写手 prompt。

设计见 docs/前情压缩设计.md，三条原则：
  ① 状态优先于剧情（写手要"现在是什么状态"，不是"发生过什么故事"）；
  ② 可判定（谁拿着什么/谁知道什么，而不是"气氛紧张"）；
  ③ 分层不重复（卷级放跨章不变的，章级只放这一章改变了什么）。

本模块只有纯函数 + prompt 组装（无 IO）：生成在 service/arq 里，便于单测。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

VOLUME_SIZE_DEFAULT = 8

# 各类字段上限（防模型啰嗦把预算吃光）
MAX_EVENTS = 6
MAX_STATE = 6
MAX_KNOWLEDGE = 4
MAX_FACTS = 4
MAX_QUESTIONS = 4
MAX_TURNS = 6
MAX_CHARS = 4
ONE_LINE_CHARS = 80
HOOK_CHARS = 100
BODY_INPUT_CHARS = 2500       # 章正文进 prompt 只取尾部（前情压缩看的是"这章改变了什么"）


def _s(val: object, limit: int = 0) -> str:
    text = " ".join(str(val or "").split())
    return text[:limit] if limit else text


def _lst(val: object) -> list:
    return list(val) if isinstance(val, (list, tuple)) else []


def volume_bounds(total_chapters: int, volume_size: int = VOLUME_SIZE_DEFAULT,
                  custom: Iterable[dict] | None = None) -> list[dict]:
    """卷边界：custom（主笔/作者微调）优先；否则按 volume_size 均分。

    返回 [{no, from, to}]，from/to 都是**1-based 章号且闭区间**。
    """
    total = max(0, int(total_chapters or 0))
    rows: list[dict] = []
    for item in _lst(custom):
        if not isinstance(item, dict):
            continue
        try:
            frm, to = int(item.get("from") or 0), int(item.get("to") or 0)
        except (TypeError, ValueError):
            continue
        if frm <= 0 or to < frm:
            continue
        rows.append({"no": int(item.get("no") or (len(rows) + 1)),
                     "from": frm, "to": min(to, total or to)})
    if rows:
        rows.sort(key=lambda r: r["from"])
        return rows
    size = max(1, int(volume_size or VOLUME_SIZE_DEFAULT))
    out: list[dict] = []
    frm = 1
    while frm <= total:
        to = min(total, frm + size - 1)
        out.append({"no": len(out) + 1, "from": frm, "to": to})
        frm = to + 1
    return out


def volume_of(chapter_no: int, bounds: list[dict]) -> dict | None:
    for b in bounds:
        if b["from"] <= int(chapter_no) <= b["to"]:
            return b
    return None


# ---------------------------------------------------------------- 章级 digest
CHAPTER_DIGEST_SCHEMA = {
    "one_line": "str 这一章用一句话说清（≤60 字，只讲结果不讲过程）",
    "events": [{"who": "str", "did": "str 具体动作（可拍）", "where": "str",
                "consequence": "str 导致了什么状态变化"}],
    "state_changes": [{"target": "str 人物名 / 道具名 / 术语",
                       "from": "str 之前的状态", "to": "str 之后的状态"}],
    "knowledge": [{"who": "str 谁知道了", "learned": "str 知道了什么", "how": "str 怎么知道的"}],
    "facts_established": ["str 本章新确立的设定/规则（不可再解释第二次）"],
    "foreshadow_touched": [{"text": "str 涉及哪条伏笔", "action": "bury|advance|close"}],
    "hook": "str 章末钩子（一句，必须让读者想翻下一页）",
    "open_questions": ["str 本章没解决、后文要回答的问题"],
    "new_entities": [{"name": "str", "kind": "character|item|term|place", "note": "str"}],
    "summary_md": "str 人读版叙事摘要 150~200 字（给作者与检索，不注入写手）",
    "conflicts": [{"type": "knowledge_leak|item_handover|other", "detail": "str 与既有前情矛盾之处"}],
}

_CHAPTER_PROMPT = """你是小说【前情压缩器】。读下面这一章正文，产出**结构化前情概要**。

硬要求：
1. 只记录**事实与状态**，不要文学描写、不要评价；
2. events 一条 = 一个可拍的动作/决定（谁做了什么、在哪、导致什么）；
3. state_changes 写**状态迁移**（持有者、身份、位置、伤势、知晓范围），这是最重要的字段；
4. knowledge 写"谁在这一章知道了什么"（后文写他不知情就是硬伤）；
5. hook 必须是那一章实际留下的悬念（没有就留空，不要编）；
6. 如果本章与【已有前情】矛盾（例如角色早该知道却不知、道具归属对不上），
   写进 conflicts，**不要自行修正**——由作者裁定；
7. summary_md 是给人看的 150~200 字叙事摘要。

只输出 JSON，字段见 schema，不要解释。

【本章】第 {chapter_no} 章《{title}》｜基调 {tone}｜字数目标 {word_target}
【已有前情（本卷概要，用于查矛盾）】
{prior}

【本章正文（尾部）】
{body}"""


def chapter_digest_prompt(*, chapter: dict, body: str, prior: str = "") -> str:
    text = str(body or "").strip()
    tail = text[-BODY_INPUT_CHARS:] if len(text) > BODY_INPUT_CHARS else text
    return _CHAPTER_PROMPT.format(
        chapter_no=int(chapter.get("order_no") or 0),
        title=_s(chapter.get("title")),
        tone=_s(chapter.get("tone")) or "未定",
        word_target=chapter.get("word_target") or "未定",
        prior=_s(prior) or "（无：这是前几章之一）",
        body=tail or "（空）",
    )


def normalize_chapter_digest(raw: object, *, chapter_no: int = 0) -> dict:
    """0-token 清洗章级 digest（字段类型兜底 + 上限截断；坏数据不抛）。"""
    d = raw if isinstance(raw, dict) else {}
    events = []
    for e in _lst(d.get("events"))[:MAX_EVENTS]:
        if not isinstance(e, dict):
            continue
        if not _s(e.get("did")):
            continue
        events.append({"who": _s(e.get("who"), 20), "did": _s(e.get("did"), 120),
                       "where": _s(e.get("where"), 30),
                       "consequence": _s(e.get("consequence"), 80)})
    states = []
    for st in _lst(d.get("state_changes"))[:MAX_STATE]:
        if not isinstance(st, dict) or not _s(st.get("target")):
            continue
        states.append({"target": _s(st.get("target"), 30),
                       "from": _s(st.get("from"), 40), "to": _s(st.get("to"), 40)})
    knowledge = []
    for k in _lst(d.get("knowledge"))[:MAX_KNOWLEDGE]:
        if not isinstance(k, dict) or not _s(k.get("who")) or not _s(k.get("learned")):
            continue
        knowledge.append({"who": _s(k.get("who"), 20), "learned": _s(k.get("learned"), 80),
                          "how": _s(k.get("how"), 40)})
    fs = []
    for f in _lst(d.get("foreshadow_touched"))[:4]:
        if isinstance(f, dict) and _s(f.get("text")):
            act = _s(f.get("action")).lower()
            fs.append({"text": _s(f.get("text"), 60),
                       "action": act if act in ("bury", "advance", "close") else "advance"})
    ents = []
    for n in _lst(d.get("new_entities"))[:6]:
        if isinstance(n, dict) and _s(n.get("name")):
            ents.append({"name": _s(n.get("name"), 30), "kind": _s(n.get("kind"), 16),
                         "note": _s(n.get("note"), 60)})
    conflicts = []
    for c in _lst(d.get("conflicts"))[:4]:
        if isinstance(c, dict) and _s(c.get("detail")):
            conflicts.append({"type": _s(c.get("type"), 24) or "other",
                              "detail": _s(c.get("detail"), 120)})
    return {
        "chapter_no": int(chapter_no or d.get("chapter_no") or 0),
        "one_line": _s(d.get("one_line"), ONE_LINE_CHARS),
        "events": events,
        "state_changes": states,
        "knowledge": knowledge,
        "facts_established": [_s(x, 80) for x in _lst(d.get("facts_established"))[:MAX_FACTS] if _s(x)],
        "foreshadow_touched": fs,
        "hook": _s(d.get("hook"), HOOK_CHARS),
        "open_questions": [_s(x, 60) for x in _lst(d.get("open_questions"))[:MAX_QUESTIONS] if _s(x)],
        "new_entities": ents,
        "summary_md": _s(d.get("summary_md")),
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------- 卷级 digest
VOLUME_DIGEST_SCHEMA = {
    "arc": "str 本卷一句话弧线（谁从哪走到哪）",
    "characters": [{"name": "str", "where": "str 当前在哪", "condition": "str 身体/处境",
                    "knows": ["str 已知道的事"], "does_not_know": ["str 明确还不知道的事"],
                    "holds": ["str 手里拿着什么关键道具"], "stance": "str 当前立场/目标"}],
    "rules": ["str 本卷确立的规则/设定"],
    "long_foreshadows": [{"text": "str", "status": "open|closed"}],
    "turning_points": ["str 关键转折（带章号）"],
    "ending_state": "str 卷末状态（下一卷从这接）",
    "summary_md": "str 人读版 200~300 字",
}

_VOLUME_PROMPT = """你是小说【前情压缩器】。把下面这一卷的**各章结构化概要**压缩成**卷级概要**。

要求：
1. 只保留跨章仍然有效的东西：人物当前状态（在哪/身体状况/知道什么/不知道什么/手里有什么）、
   已确立规则、长线伏笔、关键转折；
2. characters 里必须明确 knows 与 does_not_know —— 后文最大的硬伤是"角色知道了他不该知道的事"；
3. 不要把每一章的剧情都复述一遍（那些留在章级概要里）；
4. ending_state 要能让下一卷直接接着写；
5. 只输出 JSON。

【本卷】第 {volume_no} 卷（第 {range_from}~{range_to} 章）
【上一卷概要（可用来判断状态是否延续）】
{prior}

【各章结构化概要】
{chapters}"""


def volume_digest_prompt(*, volume_no: int, range_from: int, range_to: int,
                         chapters: list[dict], prior: str = "") -> str:
    lines: list[str] = []
    for d in chapters:
        lines.append("第 " + str(d.get("chapter_no") or "?") + " 章：" + render_chapter_digest(d, compact=True))
    return _VOLUME_PROMPT.format(
        volume_no=volume_no, range_from=range_from, range_to=range_to,
        prior=_s(prior) or "（这是第一卷）",
        chapters="\n".join(lines) or "（本卷还没有章级概要：先把章正文写出来）",
    )


def normalize_volume_digest(raw: object, *, volume_no: int = 0,
                            range_from: int = 0, range_to: int = 0) -> dict:
    d = raw if isinstance(raw, dict) else {}
    chars = []
    for c in _lst(d.get("characters"))[:MAX_CHARS]:
        if not isinstance(c, dict) or not _s(c.get("name")):
            continue
        chars.append({
            "name": _s(c.get("name"), 20), "where": _s(c.get("where"), 40),
            "condition": _s(c.get("condition"), 60),
            "knows": [_s(x, 60) for x in _lst(c.get("knows"))[:6] if _s(x)],
            "does_not_know": [_s(x, 60) for x in _lst(c.get("does_not_know"))[:6] if _s(x)],
            "holds": [_s(x, 40) for x in _lst(c.get("holds"))[:6] if _s(x)],
            "stance": _s(c.get("stance"), 60),
        })
    fs = []
    for f in _lst(d.get("long_foreshadows"))[:6]:
        if isinstance(f, dict) and _s(f.get("text")):
            st = _s(f.get("status")).lower()
            fs.append({"text": _s(f.get("text"), 60),
                       "status": st if st in ("open", "closed") else "open"})
    return {
        "volume_no": int(volume_no or 0),
        "range": [int(range_from or 0), int(range_to or 0)],
        "arc": _s(d.get("arc"), 160),
        "characters": chars,
        "rules": [_s(x, 80) for x in _lst(d.get("rules"))[:MAX_FACTS] if _s(x)],
        "long_foreshadows": fs,
        "turning_points": [_s(x, 80) for x in _lst(d.get("turning_points"))[:MAX_TURNS] if _s(x)],
        "ending_state": _s(d.get("ending_state"), 160),
        "summary_md": _s(d.get("summary_md")),
    }


# ---------------------------------------------------------------- 渲染（注入）
def render_chapter_digest(d: dict, *, compact: bool = False) -> str:
    """章级 digest → 文本（写手用的紧凑版只给事实与钩子，不给叙事摘要）。"""
    if not isinstance(d, dict) or not d:
        return ""
    parts: list[str] = []
    if d.get("one_line"):
        parts.append(_s(d["one_line"]))
    for e in (d.get("events") or [])[:4]:
        seg = "- " + _s(e.get("who")) + _s(e.get("did"))
        if e.get("where"):
            seg += "（" + _s(e.get("where")) + "）"
        if e.get("consequence"):
            seg += " → " + _s(e.get("consequence"))
        parts.append(seg)
    if d.get("state_changes"):
        parts.append("状态：" + "；".join(
            _s(x.get("target")) + " " + _s(x.get("from")) + "→" + _s(x.get("to"))
            for x in (d.get("state_changes") or [])[:4]))
    if d.get("knowledge"):
        parts.append("知晓：" + "；".join(
            _s(x.get("who")) + "知道了「" + _s(x.get("learned")) + "」"
            for x in (d.get("knowledge") or [])[:3]))
    if not compact and d.get("facts_established"):
        parts.append("新确立：" + "；".join(_s(x) for x in d["facts_established"][:3]))
    if d.get("hook"):
        parts.append("钩子：" + _s(d["hook"]))
    return " ｜ ".join(p for p in parts if p)


def render_volume_digest(d: dict) -> str:
    """卷级 digest → 文本（注入用：弧线 + 人物状态 + 规则 + 卷末状态）。"""
    if not isinstance(d, dict) or not d:
        return ""
    parts: list[str] = []
    if d.get("arc"):
        parts.append("本卷弧线：" + _s(d["arc"]))
    for c in (d.get("characters") or [])[:MAX_CHARS]:
        seg = "- " + _s(c.get("name"))
        if c.get("where"):
            seg += "（" + _s(c.get("where")) + "）"
        if c.get("condition"):
            seg += "，" + _s(c.get("condition"))
        if c.get("holds"):
            seg += "；持有：" + "、".join(_s(x) for x in c["holds"][:3])
        if c.get("knows"):
            seg += "；知道：" + "、".join(_s(x) for x in c["knows"][:3])
        if c.get("does_not_know"):
            seg += "；**不知道**：" + "、".join(_s(x) for x in c["does_not_know"][:3])
        parts.append(seg)
    if d.get("rules"):
        parts.append("规则：" + "；".join(_s(x) for x in d["rules"][:4]))
    if d.get("ending_state"):
        parts.append("卷末状态：" + _s(d["ending_state"]))
    return "\n".join(p for p in parts if p)


def render_prior_bundle(bundle: dict, *, budget: int = 1100) -> str:
    """拼注入写手 prompt 的「前情」块：全书 → 本卷 → 最近两章（越近越靠后更醒目）。"""
    sections: list[tuple[str, str, int]] = []
    book = render_volume_digest(bundle.get("book") or {})
    if book:
        sections.append(("【前情 · 全书】", book, 320))
    vol = render_volume_digest(bundle.get("volume") or {})
    if vol:
        sections.append(("【前情 · 本卷】", vol, 420))
    recent = [render_chapter_digest(d) for d in (bundle.get("recent") or []) if d]
    recent = [r for r in recent if r]
    if recent:
        sections.append(("【前情 · 最近章节】", "\n".join("- " + r for r in recent[-2:]), 400))
    out: list[str] = []
    used = 0
    for head, body, cap in sections:
        piece = head + "\n" + body[:cap]
        if used + len(piece) > budget:
            piece = piece[: max(0, budget - used)]
        if piece.strip():
            out.append(piece)
            used += len(piece)
        if used >= budget:
            break
    return "\n\n".join(out)


# ---------------------------------------------------------------- 冲突检测（0-token）
def detect_conflicts(chapter_digest: dict, volume_digest: dict) -> list[dict]:
    """新章 vs 卷级概要的**确定性**冲突检测（只查两类最致命的，宁可漏报不误报）。

    ① 信息越权：卷级明确"某人不知道 X"，本章却写他知道 X；
    ② 道具归属断链：本章把某人持有的道具写到别人手里，却没有 from→to 的状态迁移记录。
    """
    out: list[dict] = []
    vol_chars = (volume_digest or {}).get("characters") or []
    for k in (chapter_digest or {}).get("knowledge") or []:
        who = _s(k.get("who"))
        learned = _s(k.get("learned"))
        for c in vol_chars:
            if _s(c.get("name")) != who:
                continue
            for dnk in c.get("does_not_know") or []:
                if _s(dnk) and (_s(dnk) in learned or learned in _s(dnk)):
                    out.append({"type": "knowledge_leak",
                                "detail": who + " 本章得知「" + learned + "」，但卷级概要记着他还不知道「" + _s(dnk) + "」"})
    moved = {( _s(x.get("target")), _s(x.get("to")) ) for x in (chapter_digest or {}).get("state_changes") or []}
    for c in vol_chars:
        for item in c.get("holds") or []:
            for (target, to) in moved:
                if target and target == _s(item) and to and _s(c.get("name")) not in to:
                    out.append({"type": "item_handover",
                                "detail": _s(item) + " 原在 " + _s(c.get("name")) + " 手里，本章写成 " + to + "（缺一条状态迁移）"})
    return out[:4]


def has_digest(chapter: dict) -> bool:
    """章是否已有结构化前情概要（坏了当没有）。"""
    raw = (chapter or {}).get("digest_json")
    if isinstance(raw, dict):
        return bool(raw.get("one_line") or raw.get("events"))
    try:
        d = json.loads(str(raw or "{}"))
    except Exception:  # noqa: BLE001
        return False
    return isinstance(d, dict) and bool(d.get("one_line") or d.get("events"))
