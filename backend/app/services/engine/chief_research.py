"""主笔自主调研（v1.51）：排骨架前先"自己决定要看什么"，再由代码去查。

为什么不是"把全文塞进 prompt"：主笔要掌握的是**脉络**（由 book.brief 0-token 派生），
而具体细节（第 3 章那个玉佩到底怎么写的）只有主笔自己知道该查什么——这是探索性问题，
适合让模型出**检索意图**，不适合后端硬编码查询。

为什么不是"给模型一堆工具随便调"：既不可控（可能连查 10 次跑飞），也不可观测
（作者看不出它查了什么）。这里改成两段式、**有界且可审计**：
  ① 模型只做一件事：把"我还需要确认什么"写成 ≤5 条检索查询（一次廉价调用）
  ② 代码执行这些查询（本地 SQL，零 LLM），结果作为"调研笔记"回喂排骨架 prompt
好处：查询意图由模型给（真自主），执行与裁剪是确定的（不跑飞、可复现、能展示）。

降级：LLM 不可用 → fallback_queries 用 brief 里的确定性线索（逾期伏笔/断档角色/上一批章）兜底，
调研这一步**不会因为模型挂了就消失**。
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.engine.schema_retry import validate_and_retry

logger = logging.getLogger(__name__)

MAX_QUERIES = 5
HITS_PER_QUERY = 3
NOTE_CHARS = 120
BUDGET = 900

_RESEARCH_SCHEMA = [{
    "queries": [{
        "q": "str 检索查询（中文关键词 3~12 字，用于在全书记忆里查前情/角色/世界观/伏笔）",
        "why": "str 为什么要查它（≤20 字，作者会看）",
    }],
}]

_RESEARCH_PROMPT = """你是小说【主笔】。你马上要为这本书续排下一批章节骨架，先做一次**调研**：
把你还需要确认、否则可能写矛盾的事情，变成 {max_n} 条以内**检索查询**。

已有全局脉络：
{brief}

本次方向：{direction}
下一批从第 {next_no} 章开始。

要求：
1. 只输出 JSON：{{"queries": [{{"q": "...", "why": "..."}}]}}，不要解释；
2. q 要具体可检索：用**人名/物件名/术语/事件**（如「玉佩 缺口」「宁秋鹜 残图」），
   不要写「前情提要」这种空话；
3. 优先查三类：①还没回收的伏笔 ②久未出场的角色 ③上一批章节留下的状态（谁受伤/谁拿走了什么）；
4. 最多 {max_n} 条，宁少勿滥；确实没有要确认的就返回空数组。"""


def _validate_queries(raw: object) -> str | None:
    """0-token 校验：None = 通过；字符串 = 错误描述（拼回 prompt 重生成）。"""
    if not isinstance(raw, dict):
        return "顶层必须是 JSON 对象"
    qs = raw.get("queries")
    if qs is None:
        return "缺少 queries 字段"
    if not isinstance(qs, list):
        return "queries 必须是数组"
    for i, item in enumerate(qs):
        if not isinstance(item, dict):
            return f"queries[{i}] 必须是对象"
        if not str(item.get("q") or "").strip():
            return f"queries[{i}].q 不能为空"
    return None


def normalize_queries(raw: object, *, max_n: int = MAX_QUERIES) -> list[dict]:
    """把模型输出收拾成干净查询列表（去空、去重、截断）。"""
    if not isinstance(raw, dict):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw.get("queries") or []:
        if not isinstance(item, dict):
            continue
        q = " ".join(str(item.get("q") or "").split())[:40]
        if not q or q in seen:
            continue
        seen.add(q)
        why = " ".join(str(item.get("why") or "").split())[:24]
        out.append({"q": q, "why": why})
        if len(out) >= max_n:
            break
    return out


def fallback_queries(brief: dict[str, Any], *, max_n: int = MAX_QUERIES) -> list[dict]:
    """模型不可用时的确定性调研清单（0-token）：只用手上已有的线索。"""
    out: list[dict] = []
    fs = brief.get("foreshadows") or {}
    for o in (fs.get("overdue") or [])[:2]:
        text = " ".join(str(o.get("text") or "").split())[:12]
        if text:
            out.append({"q": text, "why": "伏笔逾期未收"})
    entries = (brief.get("characters") or {}).get("entries") or []
    for c in entries:
        if c.get("worst_gap") and len(out) < max_n:
            out.append({"q": str(c.get("name") or ""), "why": "角色断档 " + str(c["worst_gap"]) + " 章"})
    chapters = brief.get("chapters") or []
    for ch in [c for c in chapters if c.get("status") == "written"][-2:]:
        t = str(ch.get("title") or "").strip()
        if t and len(out) < max_n:
            out.append({"q": t[:12], "why": "上一批章要承接"})
    return [q for q in out if q["q"]][:max_n]


async def plan_queries(llm, brief_text: str, direction: str, next_no: int) -> list[dict]:
    """让主笔列出调研查询；不可用/失败 → []（调用方用 fallback_queries 兜底）。"""
    if not getattr(llm, "available", False):
        return []
    prompt = _RESEARCH_PROMPT.format(
        max_n=MAX_QUERIES, brief=brief_text or "（无）",
        direction=direction or "（沿用既有方向）", next_no=next_no)
    try:
        raw = await validate_and_retry(llm, prompt, _RESEARCH_SCHEMA, _validate_queries)
    except Exception as e:  # noqa: BLE001  调研失败不能挡住排骨架
        logger.warning("[主笔] 调研查询生成失败（改用确定性兜底）：%s", e)
        return []
    return normalize_queries(raw)


async def run_queries(repo, book_id: str, queries: list[dict]) -> list[dict]:
    """执行检索（本地 SQL，零 LLM）；查了没命中也**如实记录**，防自欺。"""
    from app.services.rag import recall as rag_recall

    notes: list[dict] = []
    for item in queries or []:
        q = str(item.get("q") or "").strip()
        if not q:
            continue
        try:
            res = await rag_recall.search_for_tool(repo, book_id, q, None, HITS_PER_QUERY)
        except Exception as e:  # noqa: BLE001  单条失败不影响其它查询
            logger.warning("[主笔] 调研查询失败 q=%s：%s", q, e)
            res = {"hits": []}
        hits = []
        for h in (res.get("hits") or [])[:HITS_PER_QUERY]:
            hits.append({"kind": h.get("kind") or "", "title": h.get("title") or "",
                         "text": " ".join(str(h.get("text") or "").split())[:NOTE_CHARS]})
        notes.append({"q": q, "why": str(item.get("why") or ""), "hits": hits})
    return notes


def render_research(notes: list[dict], *, budget: int = BUDGET) -> str:
    """渲染成喂给排骨架 prompt 的「调研笔记」（超预算截断）。"""
    lines: list[str] = []
    for n in notes or []:
        head = "- 查「" + str(n.get("q")) + "」"
        if n.get("why"):
            head += "（" + str(n["why"]) + "）"
        hits = n.get("hits") or []
        if hits:
            parts = []
            for h in hits:
                label = str(h.get("title") or "").strip()
                text = str(h.get("text") or "").strip()
                parts.append((label + "：" + text) if label and label not in text else text)
            head += " → " + " ｜ ".join(p for p in parts if p)
        else:
            head += " → （记忆里没有命中）"
        lines.append(head)
    if not lines:
        return ""
    out = "【主笔调研笔记（你要求查的内容，来自全书记忆检索）】\n" + "\n".join(lines)
    return out if len(out) <= budget else out[: budget - 1] + "…"
