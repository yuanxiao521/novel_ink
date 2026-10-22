"""按建议改写（v1.69 · 方案② 的"候选"环节）：**只产候选，不落库**。

链路：审校给出偏离项（instruction）→ 本模块让模型**只按这条建议**改，返回候选全文
→ 服务层做范围校验/差分摘要 → 落 polisher note（after 有值）→ 作者点「采纳并保存」才写库。

三条硬规则：
  1. **不改事实**：只改写法与取舍，不新增剧情、不换场景、不动人物设定；
  2. **范围可控**：scope=chapter 时要求"其余段落逐字节保持"，scope=para 时只替换指定段；
  3. **可判定**：返回 before/after 的差分统计（改了哪些段、字数变化），供前端展示与测试断言。
"""
from __future__ import annotations

import json
from typing import Any

MAX_PROMPT_BODY = 8000        # 进 prompt 的正文上限（超出取尾部 + 头部拼装见 _body_for_prompt）
CHANGED_RATIO_WARN = 0.6      # 改了超过 60% 的正文 → 结果里带警告（作者可能想只改一小段）
PARA_SEP = "\n\n"

REWRITE_SCHEMA = {
    "after": "str 改写后的正文（scope=chapter 时是整章；scope=para 时只有这一段）",
    "summary": "str 一句话说清改了什么（≤80 字，给作者看）",
    "kept": ["str 刻意保留没动的东西（帮助作者判断有没有跑偏）"],
}


def validate_rewrite(raw: object) -> str | None:
    """契约校验（0-token）。"""
    if not isinstance(raw, dict):
        return "输出必须是 JSON 对象"
    after = raw.get("after")
    if not isinstance(after, str) or not after.strip():
        return "after 必须是非空字符串（改写后的正文）"
    if not isinstance(raw.get("summary"), str) or not str(raw.get("summary")).strip():
        return "summary 必须是简要说明"
    return None


def normalize_rewrite(raw: object) -> dict:
    d = raw if isinstance(raw, dict) else {}
    kept = d.get("kept")
    return {
        "after": str(d.get("after") or "").strip(),
        "summary": " ".join(str(d.get("summary") or "").split())[:120],
        "kept": [str(x).strip()[:60] for x in (kept if isinstance(kept, list) else []) if str(x).strip()][:4],
    }


def _body_for_prompt(body: str) -> str:
    return body[-MAX_PROMPT_BODY:] if len(body) > MAX_PROMPT_BODY else body


def rewrite_prompt(*, chapter: dict, body: str, instruction: str, scope: str,
                   scope_label: str = "", context: str = "", nudge: str = "") -> str:
    """把"这条建议"变成一次可执行的改写请求。"""
    if scope == "para":
        target = f"【只改这一段】\n{body}" if scope_label else f"【只改这一段】\n{body}"
    elif scope == "chapter":
        target = ("【整章正文】（除必要处，其余段落请**逐字保持**，不要顺手润色全文）\n"
                  + _body_for_prompt(body))
    else:
        target = f"【待改正文】\n{_body_for_prompt(body)}"
    return (
        "你是这本小说的【主笔】。作者接受了一条审校建议，请你**只按这条建议**改正文。\n"
        "硬规则：\n"
        "1. 不新增剧情、不换场景、不改人物设定与事实（账本一致性优先）；\n"
        "2. 只动这条建议指向的问题；其它段落尽量逐字保持（尤其对白与已立住的画面）；\n"
        "3. 不要解释、不要加标题、不要在正文外写「建议」——after 里只放正文本身；\n"
        "4. summary 一句话说清改了什么；kept 写你刻意保留没动的东西（最多 3 条）；\n"
        "5. 只输出 JSON，字段见 schema。\n\n"
        "【schema】" + json.dumps(REWRITE_SCHEMA, ensure_ascii=False) + "\n\n"
        f"【这一章的目标（摘要）】{str(chapter.get('summary') or '').strip() or '（未填）'}\n"
        f"【张力/节奏声明】{str(chapter.get('tension_curve') or '').strip() or '（未填）'}\n"
        f"【要执行的建议】{instruction.strip()}\n"
        + (f"【上一次的问题】{nudge.strip()}\n" if nudge.strip() else "")
        + (f"【上下文（前情/本卷目标）】{context.strip()}\n" if context.strip() else "")
        + target
    )


def _paras(body: str) -> list[str]:
    return [p for p in str(body or "").split(PARA_SEP)]


def splice_scope(body: str, after: str, *, scope: str = "chapter",
                 para_index: int = 0, scene_prose: str = "") -> tuple[str, dict]:
    """把候选片段替换回全文（纯函数，保证"范围外逐字节不变"）。

    scope=chapter → after 就是全文；
    scope=para    → after 只替换第 para_index 段（1-based）；
    scope=scene   → after 只替换 scene_prose 在全文中的那一段（找不到则报错）。
    返回 (new_body, diff)；scope 非法/定位失败 → ValueError（调用方转可见错误）。
    """
    scope = str(scope or "chapter").lower()
    if scope == "chapter":
        new_body = after
        orig = body
    elif scope == "para":
        paras = _paras(body)
        if para_index <= 0 or para_index > len(paras):
            raise ValueError(f"para_index 越界：{para_index}（本文共 {len(paras)} 段）")
        orig = paras[para_index - 1]
        paras[para_index - 1] = after
        new_body = PARA_SEP.join(paras)
    elif scope == "scene":
        anchor = (scene_prose or "").strip()
        if not anchor:
            raise ValueError("scope=scene 需要 scene_prose（本场已推演正文）作为定位锚")
        if anchor not in body:
            raise ValueError("本场正文不在章正文里（可能还没并入）——先并入本章再按场改")
        orig = anchor
        new_body = body.replace(anchor, after, 1)
    else:
        raise ValueError(f"未知 scope：{scope}（可用 chapter / para / scene）")

    changed_out = sum(1 for a, b in zip(orig, after) if a != b) + abs(len(orig) - len(after))
    ratio = round(changed_out / max(1, len(orig)), 3)
    return new_body, {
        "scope": scope,
        "para_index": int(para_index or 0) if scope == "para" else 0,
        "before_chars": len(orig),
        "after_chars": len(after),
        "changed_ratio": ratio,
        "wide_change": bool(scope != "chapter" and ratio >= CHANGED_RATIO_WARN),
        "total_chars": len(new_body),
    }
