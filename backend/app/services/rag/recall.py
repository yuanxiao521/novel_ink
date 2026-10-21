"""记忆召回（v1.49 · 产品层）：把检索结果装配成**写手 prompt 能直接吃的素材**。

这件事解决的是一个具体缺陷：直写链路里写第 N 章时上下文只带「上一章结尾 600 字」，
第 10 章对前 9 章几乎失忆（人设漂移 / 伏笔断线）。而每章摘要、角色卡、世界状态、
伏笔账本**早就在库里**——只是从没被按需取出来过。

两段式设计（关键：检索失败不能让功能消失）：
  ① 检索优先：混合检索（关键词 RRF 向量）按当前章的目标召回「相关」素材
  ② 兜底直取：某一路检索为空（没建索引 / DB 不可用 / 无匹配词）时，
     按**时间序**直接取最近的前情 + 全部角色/世界观/未回收伏笔
     → 所以即使一次 reindex 都没跑过，「前情提要」也照样有内容

输出统一是 dict（前端「记忆」面板与后端 prompt 共用同一份结构，口径不分叉）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.rag import embedder, store
from app.services.rag.indexer import _char_text

logger = logging.getLogger(__name__)

# 各段字符预算（总预算 ~1800 字：写手 prompt 里不能把正文上下文挤掉）
BUDGET = {"prev": 700, "characters": 420, "world": 420, "foreshadow": 260}
ITEM_CHARS = {"prev": 190, "characters": 150, "world": 110, "foreshadow": 110}


def _clip(text: str, limit: int) -> str:
    body = " ".join(str(text or "").split())
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _scene_text(scenes: list[dict]) -> str:
    parts = []
    for sc in scenes or []:
        parts.append(" ".join(str(x) for x in (sc.get("title"), sc.get("goal"),
                                               sc.get("content_desc")) if str(x or "").strip()))
    return " ".join(p for p in parts if p)


async def _query_vector(text: str) -> Optional[list[float]]:
    """查询向量（可选）：embedding 不可用 → None → 检索自动只用关键词腿。"""
    if not embedder.available() or not text.strip():
        return None
    vecs = await embedder.aembed([text])
    if not vecs:
        return None
    return vecs[0]


async def _hit(book_id: str, query: str, kinds: list[str], limit: int,
               query_vec: Optional[list[float]],
               chapter_no_lt: Optional[int] = None) -> list[dict]:
    return await store.search(book_id, query, kinds=kinds, limit=limit,
                              query_vec=query_vec, chapter_no_lt=chapter_no_lt)


def _fallback_prev(chapters: list[dict], cur_no: int, limit: int = 3) -> list[dict]:
    """没索引时的前情兜底：取最近 3 章有摘要/正文的（时间序倒排）。"""
    out: list[dict] = []
    for ch in sorted(chapters, key=lambda c: int(c.get("order_no") or 0), reverse=True):
        no = int(ch.get("order_no") or 0)
        if no >= cur_no or no <= 0:
            continue
        summary = str(ch.get("summary") or "").strip()
        body = str(ch.get("prose_md") or "").strip()
        text = summary or (("本章写到：" + body[-260:]) if body else "")
        if not text:
            continue
        out.append({"id": "fb-" + str(ch.get("id")), "kind": "chapter",
                    "chapter_no": no, "title": "第 " + str(no) + " 章 " + str(ch.get("title") or ""),
                    "text": text, "score": 0.0})
        if len(out) >= limit:
            break
    return out


def _fallback_characters(characters: list[dict], limit: int = 4) -> list[dict]:
    return [{"id": "fb-" + str(c.get("id")), "kind": "character", "chapter_no": 0,
             "title": str(c.get("name") or ""), "text": _char_text(c), "score": 0.0}
            for c in characters[:limit]]


def _fallback_world(states: list[dict], limit: int = 6) -> list[dict]:
    return [{"id": "fb-" + str(s.get("id")), "kind": "world",
             "chapter_no": int(s.get("scene_no") or 0),
             "title": str(s.get("name") or ""),
             "text": (str(s.get("kind") or "") + " " + str(s.get("name") or "")
                      + "：" + str(s.get("value") or "")).strip(),
             "score": 0.0} for s in states[:limit]]


def _fallback_foreshadows(items: list[dict], limit: int = 4) -> list[dict]:
    open_items = [f for f in items if str(f.get("status") or "") != "closed"]
    return [{"id": "fb-" + str(f.get("id")), "kind": "foreshadow", "chapter_no": 0,
             "title": "伏笔（" + str(f.get("status") or "") + "）",
             "text": str(f.get("text") or ""), "score": 0.0}
            for f in open_items[:limit]]


async def chapter_recall(repo, book_id: str, chapter_id: str) -> dict[str, Any]:
    """装配一章的「记忆素材」：前情 / 角色 / 世界观 / 未回收伏笔 + 检索模式。"""
    empty = {"prev": [], "characters": [], "world": [], "foreshadows": [],
             "mode": "empty", "query": "", "counts": {}, "chars": 0}
    if not book_id or not chapter_id:
        return empty
    try:
        chapter = await repo.get_chapter(chapter_id)
        chapters = await repo.list_chapters_by_book(book_id)
        scenes = await repo.list_scenes_by_chapter(chapter_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[rag] 召回取数失败 %s：%s", chapter_id, e)
        return empty
    if not chapter:
        return empty
    cur_no = int(chapter.get("order_no") or 0)
    query = " ".join(str(x) for x in (chapter.get("title"), chapter.get("summary"),
                                      _scene_text(scenes)) if str(x or "").strip())
    qvec = await _query_vector(query)

    prev = await _hit(book_id, query, ["chapter"], 6, qvec, chapter_no_lt=cur_no)
    chars = await _hit(book_id, query, ["character"], 4, qvec)
    world = await _hit(book_id, query, ["world"], 5, qvec)
    fs = await _hit(book_id, query, ["foreshadow"], 4, qvec)
    # 四段里真正由检索命中的段数（0 = 全靠兜底直取，前端要能看出来）
    from_search = sum(1 for x in (prev, chars, world, fs) if x)
    mode = "hybrid" if qvec else "keyword"
    if from_search == 0:
        mode = "fallback"

    # 兜底直取（只补空的那几段；不覆盖检索结果）
    if not prev:
        try:
            prev = _fallback_prev(chapters, cur_no)
        except Exception:  # noqa: BLE001
            prev = []
    if not chars:
        try:
            chars = _fallback_characters(await repo.list_characters_by_book(book_id))
        except Exception:  # noqa: BLE001
            chars = []
    if not world:
        try:
            world = _fallback_world(await repo.list_world_states(book_id))
        except Exception:  # noqa: BLE001
            world = []
    if not fs:
        try:
            fs = _fallback_foreshadows(await repo.list_foreshadows(book_id))
        except Exception:  # noqa: BLE001
            fs = []
    # 已回收伏笔不占额度（检索按相关度可能把 closed 也捞回来）
    fs = [f for f in fs if "closed" not in str(f.get("title") or "")]

    bundle = {"prev": prev, "characters": chars, "world": world, "foreshadows": fs,
              "mode": mode, "from_search": from_search,
              "query": _clip(query, 200),
              "counts": {"prev": len(prev), "characters": len(chars),
                         "world": len(world), "foreshadows": len(fs)}}
    bundle["chars"] = len(render(bundle))
    return bundle


def render(bundle: dict[str, Any]) -> str:
    """把素材渲染成 prompt 段落（带预算裁剪：宁可少给，不许挤掉正文上下文）。"""
    sections = (
        ("prev", "【记忆检索 · 前情提要】"),
        ("characters", "【记忆检索 · 相关角色】"),
        ("world", "【记忆检索 · 世界观 / 世界状态】"),
        ("foreshadows", "【记忆检索 · 未回收伏笔】"),
    )
    blocks: list[str] = []
    for key, head in sections:
        budget = BUDGET.get(key, 300)
        limit = ITEM_CHARS.get(key, 110)
        lines: list[str] = []
        used = 0
        for item in bundle.get(key) or []:
            label = str(item.get("title") or "").strip()
            text = _clip(item.get("text") or "", limit)
            if not text:
                continue
            line = ("- " + label + "：" + text) if label and label not in text else ("- " + text)
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line)
        if lines:
            blocks.append(head + "\n" + "\n".join(lines))
    return "\n\n".join(blocks)


async def search_for_tool(repo, book_id: str, query: str, kinds: Optional[list[str]] = None,
                          top_k: int = 8) -> dict[str, Any]:
    """工具 `memory.search` 的实现：给主笔/责编/作者在对话里查记忆。"""
    body = str(query or "").strip()
    if not body:
        return {"query": "", "hits": [], "mode": "empty", "note": "查询为空"}
    qvec = await _query_vector(body)
    hits = await store.search(book_id, body, kinds=kinds, limit=max(1, min(30, top_k)),
                              query_vec=qvec)
    return {"query": body, "kinds": kinds or [], "mode": "hybrid" if qvec else "keyword",
            "hits": hits, "count": len(hits)}
