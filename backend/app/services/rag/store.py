"""记忆检索的存储层（v1.49 · 移植自 agent_project/app/db/repositories/chunk_repo.py）。

与原实现的差异（都是「适配本仓库」的必要改动，不是偷懒）：
  1. **会话注入**：原实现 import 模块级 AsyncSessionLocal；这里每次调用自己开一个会话
     （`get_session_factory()`），既不改仓库现有的 Repo 约定，也不会在 arq 进程里多一个池。
  2. 去掉 RBAC：`_permission_where`（varchar[] && 权限标签）在本项目没有对应概念，直接删。
  3. 表按业务切了 kind（chapter/character/world/foreshadow/belief/memory/scene），
     检索时按 kind 过滤 + 权重，而不是"一整本 PDF 的切片"。
  4. `tsv` 是**生成列**（GENERATED ALWAYS AS ... STORED），写入时不用管；
     关键词查询用 ts_rank（不是 BM25，与原实现一致）。

降级原则：DB 不可用 / SQL 出错一律返回空列表并记日志，**绝不打断写作链路**。
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from sqlalchemy import func, or_, select

from app.config import settings
from app.db.models.memory_chunk import MemoryChunk
from app.services.rag.fusion import apply_kind_weight, rrf_fuse
# v1.62：哪些 kind 受「章号时间轴」约束由 kinds.py 统一声明（新增 kind 只改那一处）。
from app.services.rag.kinds import CHAPTER_SCOPED_KINDS

logger = logging.getLogger(__name__)

TS_CONFIG = "zh"            # 0019 迁移创建的中文分词配置（无 zhparser 时退化为 simple）
KEYWORD_CANDIDATES = 30     # 单路召回上限（与原实现 retrieval_recall_top_k=20 同量级）
VECTOR_CANDIDATES = 30

def _chapter_scope(chapter_no_lt: Optional[int]):
    """章级素材的时间闸：非章级素材不受限；章级素材只取**当前章之前**的。

    为什么单独抽函数：关键词腿和向量腿都要用同一条规则，写两遍必然有一天只改一处
    （v1.49 就是按「kind != 'chapter'」写的，v1.62 新增章级 kind 后会漏后文）。
    """
    if chapter_no_lt is None:
        return None
    return or_(MemoryChunk.kind.notin_(list(CHAPTER_SCOPED_KINDS)),
               MemoryChunk.chapter_no < int(chapter_no_lt))


def _factory():
    """取会话工厂；persist=False（纯内存态测试）返回 None。"""
    if not settings.persist:
        return None
    from app.db.engine import get_session_factory

    return get_session_factory()


def _row_dict(row: MemoryChunk, score: float) -> dict[str, Any]:
    return {"id": row.id, "kind": row.kind, "ref_id": row.ref_id or "",
            "chapter_no": int(row.chapter_no or 0), "title": row.title or "",
            "text": row.text or "", "ts": int(row.ts or 0),
            "score": round(float(score), 6)}


async def delete_book(book_id: str) -> int:
    """清掉一本书的全部记忆块（重建前调用）。"""
    factory = _factory()
    if factory is None or not book_id:
        return 0
    try:
        async with factory() as session:
            rows = (await session.execute(
                select(MemoryChunk.id).where(MemoryChunk.book_id == book_id))).scalars().all()
            if rows:
                for cid in rows:
                    obj = await session.get(MemoryChunk, cid)
                    if obj is not None:
                        await session.delete(obj)
                await session.commit()
            return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("[rag] 清空记忆块失败 book=%s：%s", book_id, e)
        return 0


async def replace_book(book_id: str, rows: list[dict[str, Any]]) -> int:
    """整本替换（先删后插）：重建是幂等的，不需要增量 diff。"""
    factory = _factory()
    if factory is None or not book_id:
        return 0
    try:
        async with factory() as session:
            old = (await session.execute(
                select(MemoryChunk).where(MemoryChunk.book_id == book_id))).scalars().all()
            for obj in old:
                await session.delete(obj)
            await session.flush()
            for r in rows:
                session.add(MemoryChunk(
                    id=r["id"], book_id=book_id, kind=r["kind"],
                    ref_id=str(r.get("ref_id") or ""),
                    chapter_no=int(r.get("chapter_no") or 0),
                    title=str(r.get("title") or "")[:200],
                    text=str(r.get("text") or ""),
                    meta_json=str(r.get("meta_json") or "{}"),
                    ts=int(r.get("ts") or 0),
                    embedding=r.get("embedding"),
                ))
            await session.commit()
            return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("[rag] 写入记忆块失败 book=%s：%s", book_id, e)
        return 0


async def _keyword_leg(session, book_id: str, query: str, kinds,
                       chapter_no_lt: Optional[int]) -> list[tuple[MemoryChunk, float]]:
    """关键词腿：中文分词 → OR 拼接 tsquery → ts_rank 排序（移植自原 keyword_search）。"""
    tokens = func.tsvector_to_array(func.to_tsvector(TS_CONFIG, query))
    joined = func.nullif(func.array_to_string(tokens, " | "), "")
    tsquery = func.to_tsquery(TS_CONFIG, joined)
    rank = func.ts_rank(MemoryChunk.tsv, tsquery)
    stmt = select(MemoryChunk, rank.label("rank")).where(
        MemoryChunk.book_id == book_id, MemoryChunk.tsv.op("@@")(tsquery))
    if kinds:
        stmt = stmt.where(MemoryChunk.kind.in_(list(kinds)))
    scope = _chapter_scope(chapter_no_lt)
    if scope is not None:
        # 「前情」只看当前章之前的：非章级素材（角色/世界观/伏笔）不受限
        stmt = stmt.where(scope)
    stmt = stmt.order_by(rank.desc()).limit(KEYWORD_CANDIDATES)
    rows = (await session.execute(stmt)).all()
    return [(r[0], float(r[1] or 0.0)) for r in rows]


async def _vector_leg(session, book_id: str, query_vec: Sequence[float], kinds,
                      chapter_no_lt: Optional[int]) -> list[tuple[MemoryChunk, float]]:
    """向量腿：cosine_distance 升序（移植自原 vector_search）。"""
    distance = MemoryChunk.embedding.cosine_distance(list(query_vec))
    stmt = select(MemoryChunk, distance.label("d")).where(
        MemoryChunk.book_id == book_id, MemoryChunk.embedding.isnot(None))
    if kinds:
        stmt = stmt.where(MemoryChunk.kind.in_(list(kinds)))
    if chapter_no_lt is not None:
        stmt = stmt.where(or_(MemoryChunk.kind != "chapter",
                              MemoryChunk.chapter_no < int(chapter_no_lt)))
    stmt = stmt.order_by(distance.asc()).limit(VECTOR_CANDIDATES)
    rows = (await session.execute(stmt)).all()
    return [(r[0], float(r[1] or 0.0)) for r in rows]


async def search(book_id: str, query: str, *, kinds: Optional[list[str]] = None,
                 limit: int = 8, query_vec: Optional[Sequence[float]] = None,
                 chapter_no_lt: Optional[int] = None) -> list[dict[str, Any]]:
    """混合检索（关键词必跑，向量有就跑）→ RRF 融合 → kind 加权 → 取前 limit。

    DB 不可用/出错 → []（调用方回退到"按时间直取"，功能不消失）。
    """
    factory = _factory()
    if factory is None or not book_id:
        return []
    body = str(query or "").strip()
    ranked: dict[str, list[str]] = {}
    hits: dict[str, MemoryChunk] = {}
    try:
        async with factory() as session:
            if body:
                kw = await _keyword_leg(session, book_id, body, kinds, chapter_no_lt)
                if kw:
                    ranked["keyword"] = [r.id for r, _ in kw]
                    hits.update({r.id: r for r, _ in kw})
            if query_vec:
                vec = await _vector_leg(session, book_id, query_vec, kinds, chapter_no_lt)
                if vec:
                    ranked["vector"] = [r.id for r, _ in vec]
                    hits.update({r.id: r for r, _ in vec})
    except Exception as e:  # noqa: BLE001
        logger.warning("[rag] 检索失败 book=%s：%s", book_id, e)
        return []
    if not ranked:
        return []
    fused = apply_kind_weight(rrf_fuse(ranked), {cid: r.kind for cid, r in hits.items()})
    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:max(1, int(limit))]
    return [_row_dict(hits[cid], score) for cid, score in ordered]


async def stats(book_id: str) -> dict[str, Any]:
    """一本书的记忆块统计（前端「记忆」面板 / /health 用）。"""
    empty = {"chunks": 0, "by_kind": {}, "vectors": 0, "indexed_at": 0}
    factory = _factory()
    if factory is None or not book_id:
        return empty
    try:
        async with factory() as session:
            rows = (await session.execute(
                select(MemoryChunk.kind, func.count(MemoryChunk.id),
                       func.count(MemoryChunk.embedding), func.max(MemoryChunk.updated_at))
                .where(MemoryChunk.book_id == book_id)
                .group_by(MemoryChunk.kind))).all()
    except Exception as e:  # noqa: BLE001
        logger.warning("[rag] 统计失败 book=%s：%s", book_id, e)
        return empty
    by_kind: dict[str, int] = {}
    vectors = 0
    last: list[Any] = []
    for kind, cnt, vec_cnt, updated in rows:
        by_kind[str(kind)] = int(cnt or 0)
        vectors += int(vec_cnt or 0)
        if updated is not None:
            last.append(updated)
    return {"chunks": sum(by_kind.values()), "by_kind": by_kind, "vectors": vectors,
            "indexed_at": int(max(last).timestamp() * 1000) if last else 0}
