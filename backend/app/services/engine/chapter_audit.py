"""章级骨架遵循度审计（v1.67 · 工具 prose.audit）：**只读正文，对照骨架，不改一个字**。

作者诉求（路线图 P3.7-B）：主笔能用 book.brief 看骨架，却看不到正文——于是无法回答
「这一章有没有完成我定的目标」。本模块给主笔（与章级副页）一只只读的眼睛，两段式：

  ① 0-token 判定（确定性、可复现、零成本）：
     字数目标偏差 / 张力曲线缺失 / 必然事件（beats）未达成 / 场正文未并入 / 章摘为空 / 主角缺席
  ② LLM 判定（语义）：
     对照「章摘要（=章目标）+ 本卷目标 + 张力曲线」，指出偏离项并给**可执行的改法建议**

硬边界：本模块只产出报告；**不写正文、不改骨架**（写库只写一条审计 note）。
"""
from __future__ import annotations

import json
from typing import Any

WORD_TOL_LOW = 0.7          # 低于目标 70% → 欠量
WORD_TOL_HIGH = 1.3         # 高于目标 130% → 超量跑偏（"写飞了"）
AUDIT_BODY_CHARS = 6000     # 进 prompt 的正文上限（尾部优先：结局偏离最要紧）
MAX_ITEMS = 6

AUDIT_SCHEMA = {
    "verdict": "str 一句话结论（这一章是否完成了章目标/卷目标，≤50 字）",
    "score": "int 0-100 骨架遵循度",
    "items": [{
        "kind": "str 偏离类型：goal_missed|tension_off|volume_off|character_off|hook_missing|other",
        "detail": "str 具体偏离（引用正文现象，不要泛泛而谈）",
        "suggest": "str 怎么改（可执行的下一步，不要求改字时写「保留」）",
        "severity": "str high|mid|low",
    }],
    "keep": ["str 这一章做得对、后文必须保持的东西"],
}

SEVERITY_ORDER = {"high": 0, "mid": 1, "low": 2}


def _s(val: object, limit: int = 0) -> str:
    text = " ".join(str(val or "").split())
    return text[:limit] if limit else text


def _llist(val: object) -> list:
    return list(val) if isinstance(val, (list, tuple)) else []


def _unmet_beats(scenes: list[dict]) -> list[dict]:
    """从各场 plan_cfg_json.beats_status 里捞「推演完但必然事件没达成」的条目。"""
    out: list[dict] = []
    for i, sc in enumerate(scenes, start=1):
        try:
            cfg = json.loads(str(sc.get("plan_cfg_json") or "{}"))
        except Exception:  # noqa: BLE001
            cfg = {}
        if not isinstance(cfg, dict):
            continue
        st = cfg.get("beats_status")
        if not isinstance(st, dict) or not st or st.get("ok"):
            continue
        for text in _llist(st.get("missed_texts"))[:3]:
            if _s(text):
                out.append({"scene_no": i, "title": _s(sc.get("title")), "text": _s(text, 60)})
    return out


def checks_zero_token(*, chapter: dict, body: str, scenes: list[dict],
                      volume: dict | None = None,
                      protagonist_names: list[str] | None = None) -> list[dict]:
    """0-token 骨架偏离判定（每条带 code/level/detail/suggest，前端直接可读）。"""
    checks: list[dict] = []
    chars = len(body.replace(" ", "").replace("\n", ""))
    target = int(chapter.get("word_target") or 0)

    if chars == 0:
        checks.append({"code": "empty", "level": "high",
                       "detail": "章正文为空（还没写）",
                       "suggest": "先点「✍ 写这一章」出稿，或手写一段再审计"})
    if target > 0 and chars > 0:
        ratio = chars / target
        if ratio < WORD_TOL_LOW:
            checks.append({"code": "words_under", "level": "mid",
                           "detail": f"字数欠量：{chars} / 目标 {target}（{int(ratio * 100)}%）",
                           "suggest": "补一到两段（优先补章目标里没交代的事），或下调字数目标"})
        elif ratio > WORD_TOL_HIGH:
            checks.append({"code": "words_over", "level": "low",
                           "detail": f"字数超量：{chars} / 目标 {target}（{int(ratio * 100)}%）",
                           "suggest": "砍掉不推进目标的段落；超量常见于「演了两遍同一件事」"})
    elif target == 0:
        checks.append({"code": "no_target", "level": "mid",
                       "detail": "这一章没有字数目标（无从判断时代/体量）",
                       "suggest": "去主笔页给这一章补字数目标（骨架体检会一直报这条）"})

    if not _s(chapter.get("tension_curve")):
        checks.append({"code": "no_curve", "level": "low",
                       "detail": "没有张力曲线声明（写手/ auditor 都不知道这段该升还是该落）",
                       "suggest": "补一句节奏声明（如「开篇压→中段转→章末扬」）"})
    if not _s(chapter.get("summary")):
        checks.append({"code": "no_goal", "level": "high",
                       "detail": "章摘要（章目标）为空——审计没有可对照的目标",
                       "suggest": "先「🧩 补全设定」或手写一句本章要完成什么"})

    unmet = _unmet_beats(scenes)
    if unmet:
        checks.append({"code": "beats_unmet", "level": "high",
                       "detail": "必然事件未达成：" + "；".join(
                           f"第 {u['scene_no']} 场《{u['title']}》{u['text']}" for u in unmet[:3]),
                       "suggest": "这些事件是硬约束：要么补写，要么回推演台重演那场"})

    merged = [s for s in scenes if _s(s.get("final_prose"))]
    not_in = [s for s in merged if _s(s.get("final_prose")) not in body]
    if not_in:
        checks.append({"code": "scene_not_merged", "level": "mid",
                       "detail": f"{len(not_in)} 场已推演但正文里没有并入（可能是漏并，也可能是重写了）",
                       "suggest": "左栏「⇥ 并入本章」可一键并入；若是有意重写，可忽略这条"})

    names = [n for n in (protagonist_names or []) if _s(n)]
    if names and body and not any(n in body for n in names):
        checks.append({"code": "protagonist_absent", "level": "high",
                       "detail": "主角没出现在正文里：" + "、".join(names[:3]),
                       "suggest": "确认是否写错了视角，或主角本章确实不出场（那就该在摘要里说明）"})

    vol = volume or {}
    if vol and not _s(vol.get("arc_goal")):
        checks.append({"code": "volume_no_goal", "level": "low",
                       "detail": "本卷没有卷目标（arc_goal 为空，无法判断这一章是否在推进本卷）",
                       "suggest": "去主笔页分卷编辑器补卷目标"})
    return checks


def audit_prompt(*, chapter: dict, body: str, volume: dict | None,
                 checks: list[dict], prior: str = "") -> str:
    """LLM 判定用的 prompt：只让它判断「是否偏离骨架」，不许改写正文。"""
    vol = volume or {}
    check_lines = "\n".join(f"- [{c['level']}] {c['detail']}" for c in checks) or "（0-token 未发现问题）"
    return (
        "你是这本小说的【主笔】。现在只做一件事：**判断这一章有没有偏离你定的骨架**，并给出改法建议。\n"
        "硬规则：\n"
        "1. 你不改正文、不重写、不润色；只指出偏离与建议（作者决定改不改）；\n"
        "2. 判断依据只有三样：章摘要（=这一章要完成什么）、本卷目标、张力/节奏声明；\n"
        "3. 每条偏离必须引用正文现象（哪个段落写了什么），不要泛泛而谈；\n"
        "4. 如果没有偏离，items 留空数组即可；keep 写「这一章做得对、后文要保持的东西」；\n"
        "5. 只输出 JSON，字段见 schema。\n\n"
        "【schema】" + json.dumps(AUDIT_SCHEMA, ensure_ascii=False) + "\n\n"
        f"【第 {int(chapter.get('order_no') or 0)} 章目标（摘要）】{_s(chapter.get('summary')) or '（空）'}\n"
        f"【张力/节奏声明】{_s(chapter.get('tension_curve')) or '（空）'}\n"
        f"【本卷目标】{_s(vol.get('arc_goal')) or '（未设）'}\n"
        f"【本卷卷末高潮】{_s(vol.get('climax')) or '（未设）'}\n"
        f"【前情（节选）】{_s(prior) or '（无）'}\n"
        f"【0-token 已判定的问题】\n{check_lines}\n\n"
        "【本章正文】\n" + (body[-AUDIT_BODY_CHARS:] if body else "（空）")
    )


def validate_audit(raw: object) -> str | None:
    """契约校验：返回 None=通过，返回字符串=错误描述（拼回 prompt 重生成）。"""
    if not isinstance(raw, dict):
        return "输出必须是 JSON 对象"
    if not _s(raw.get("verdict")):
        return "缺少 verdict（一句话结论）"
    items = raw.get("items")
    if items is not None and not isinstance(items, list):
        return "items 必须是数组（可为空）"
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            return f"items[{i}] 必须是对象"
        if not _s(it.get("detail")):
            return f"items[{i}] 缺少 detail（具体偏离现象）"
    return None


def normalize_audit(raw: object) -> dict:
    """0-token 清洗 LLM 审计结果（坏数据不抛，字段截断）。"""
    d = raw if isinstance(raw, dict) else {}
    items = []
    for it in _llist(d.get("items"))[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        detail = _s(it.get("detail"), 240)
        if not detail:
            continue
        sev = _s(it.get("severity")).lower()
        items.append({
            "kind": _s(it.get("kind"), 24) or "other",
            "detail": detail,
            "suggest": _s(it.get("suggest"), 200),
            "severity": sev if sev in SEVERITY_ORDER else "mid",
        })
    items.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 1))
    try:
        score = int(d.get("score"))
    except (TypeError, ValueError):
        score = None
    return {
        "verdict": _s(d.get("verdict"), 120),
        "score": None if score is None else max(0, min(100, score)),
        "items": items,
        "keep": [_s(x, 120) for x in _llist(d.get("keep"))[:4] if _s(x)],
    }


def render_audit(report: dict) -> str:
    """一页纸（给主笔对话 / 前端复制 / 审计 note 落库用）。"""
    dev = report.get("deviation") or {}
    lines: list[str] = []
    if dev.get("verdict"):
        lines.append(f"结论：{dev['verdict']}" + (f"（遵循度 {dev['score']}）" if dev.get("score") is not None else ""))
    for c in report.get("checks") or []:
        lines.append(f"- [{c.get('level')}] {c.get('detail')}")
    for it in dev.get("items") or []:
        lines.append(f"- [{it.get('severity')}] {it.get('detail')}" + (f" → {it.get('suggest')}" if it.get("suggest") else ""))
    if dev.get("keep"):
        lines.append("保持：" + "；".join(dev["keep"]))
    return "\n".join(lines)
