"""记忆索引器（v1.49）：把「已在库里但从来没被检索过」的素材切成记忆块。

索引哪些 kind（对应产品里的「主要业务」）：
  chapter     章标题 + 章摘要 + 章正文结尾 500 字  → 「前情提要」
  scene       场标题 + 舞台 + 目标 + 内容描述 + 场摘要 → 「上一场发生了什么」
  character   角色卡（spec_json 全字段摊平）        → 「相关角色（腔调/身份/秘密）」
  world       世界状态账本（kind/name/value）        → 「世界观 / 当前世界状态」
  foreshadow  伏笔三态（buried/in_progress/closed）  → 「未回收伏笔」
  belief      角色信念（只索引 text）                → 「角色以为的事」
  memory      书级记忆（direction/setting/constraint）→ 「作者定过的方向与约束」

为什么不做"真·增量"：一本书的素材总量是百级（不是 PDF 的万级），全量重建 ~50ms；
增量 diff 的复杂度换不来时间，反而容易留脏块（重复/漏删）。**幂等重建**优先。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.services.rag import embedder, store
from app.services.rag.chunker import split_text

logger = logging.getLogger(__name__)

MAX_CHAPTER_TAIL = 500      # 章正文只索引结尾：承接要的是"写到哪了"，不是全文
MAX_ENTITY_CHARS = 600      # 单条素材过长就切块（角色卡/世界状态一般远小于此）


def _cid(book_id: str, kind: str, ref_id: str, idx: int) -> str:
    """稳定 id：同一素材重建后 id 不变（便于对比调试，也让写入天然幂等）。"""
    raw = (book_id + "|" + kind + "|" + ref_id + "|" + str(idx)).encode("utf-8")
    return "mem-" + hashlib.sha1(raw).hexdigest()[:16]


def _ms(value: Any) -> int:
    """把 updated_at（str/datetime/None）折成毫秒时间戳，失败给 0（不抛）。"""
    if not value:
        return 0
    try:
        from datetime import datetime

        if isinstance(value, datetime):
            return int(value.timestamp() * 1000)
        return int(datetime.fromisoformat(str(value)).timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return 0


# 这些字段是技术字段，写进 prompt 只会挤掉真正的角色信息
_SKIP_SPEC_KEYS = {"id", "book_id", "scene_id", "name", "created_at", "updated_at", "embedding"}


def _flatten(val: Any) -> str:
    """把角色卡里的任意值摊平成一句人话（dict/list/JSON 字符串都要能被读懂）。

    为什么必须处理：spec_json 里的 dynamic_goals 之类是"JSON 字符串"，
    直接 str() 进 prompt 就是 {"id": "g-116c2b", ...} 这种机器噪音，
    实测会把真正有用的 summary/voice 挤到裁剪线外。
    """
    if val is None:
        return ""
    if isinstance(val, str):
        body = val.strip()
        if body[:1] in ("[", "{"):
            try:
                val = json.loads(body)
            except Exception:  # noqa: BLE001  不是合法 JSON，就当普通文本
                return body
        else:
            return body
    if isinstance(val, (list, tuple)):
        bits: list[str] = []
        for item in val:
            if isinstance(item, dict):
                inner = item.get("text") or item.get("content") or item.get("label") or ""
                bits.append(str(inner).strip() if inner else "")
            else:
                bits.append(str(item).strip())
        return "、".join(b for b in bits if b)
    if isinstance(val, dict):
        inner = val.get("text") or val.get("content") or ""
        if inner and len(val) <= 2:
            return str(inner).strip()
        return "；".join(str(k) + "：" + str(v) for k, v in val.items() if str(v).strip())
    return str(val).strip()


def _char_text(ch: dict) -> str:
    """角色卡摊平成文本：保留人设字段，丢掉技术字段（名字由 chunk.title 承载，不重复）。"""
    try:
        spec = json.loads(ch.get("spec_json") or "{}")
    except Exception:  # noqa: BLE001
        spec = {}
    if not isinstance(spec, dict):
        spec = {}
    parts: list[str] = []
    for key, val in spec.items():
        if str(key) in _SKIP_SPEC_KEYS:
            continue
        text = _flatten(val)
        if text:
            parts.append(str(key) + "：" + text)
    return "\n".join(parts)


def _push(rows: list[dict], book_id: str, kind: str, ref_id: str, title: str,
          text: str, *, chapter_no: int = 0, ts: int = 0,
          meta: dict | None = None) -> None:
    """把一条素材切块后推进 rows（长文本按句读切，短文本就一块）。"""
    body = (text or "").strip()
    if not body and not (title or "").strip():
        return
    chunks = split_text(body) if len(body) > MAX_ENTITY_CHARS else ([body] if body else [""])
    for idx, chunk in enumerate(chunks):
        rows.append({
            "id": _cid(book_id, kind, ref_id, idx), "kind": kind, "ref_id": ref_id,
            "chapter_no": int(chapter_no or 0),
            "title": (title or "")[:200], "text": chunk,
            "meta_json": json.dumps(meta or {}, ensure_ascii=False)[:2000], "ts": int(ts or 0),
        })


async def build_rows(repo, book_id: str, chapter_id: str = "") -> list[dict]:
    """从现有表里抽出所有可检索素材（纯装配，不写库）。chapter_id 非空时只装该章的章/场。"""
    rows: list[dict] = []
    chapters = await repo.list_chapters_by_book(book_id)
    no_by_chapter = {str(c.get("id")): int(c.get("order_no") or 0) for c in chapters}

    for ch in chapters:
        if chapter_id and str(ch.get("id")) != chapter_id:
            continue
        no = int(ch.get("order_no") or 0)
        body = str(ch.get("prose_md") or "").strip()
        tail = body[-MAX_CHAPTER_TAIL:] if body else ""
        summary = str(ch.get("summary") or "").strip()
        text = "\n".join(x for x in (summary, ("本章写到：" + tail) if tail else "") if x)
        title = str(ch.get("title") or "").strip()
        if not title.startswith("第"):        # 章标题常已自带「第一章 …」，别再串一遍
            title = ("第 " + str(no) + " 章 " + title).strip()
        _push(rows, book_id, "chapter", str(ch.get("id") or ""), title, text,
              chapter_no=no, ts=_ms(ch.get("updated_at")),
              meta={"tone": ch.get("tone") or "", "chars": len(body)})

    for ch in chapters:
        cid = str(ch.get("id") or "")
        if chapter_id and cid != chapter_id:
            continue
        for sc in (ch.get("scenes") or []):
            text = "\n".join(x for x in (
                ("舞台：" + str(sc.get("stage_desc") or "").strip()) if sc.get("stage_desc") else "",
                ("本场目标：" + str(sc.get("goal") or "").strip()) if sc.get("goal") else "",
                ("内容：" + str(sc.get("content_desc") or "").strip()) if sc.get("content_desc") else "",
                ("场摘要：" + str(sc.get("scene_summary") or "").strip()) if sc.get("scene_summary") else "",
            ) if x)
            _push(rows, book_id, "scene", str(sc.get("id") or ""), str(sc.get("title") or ""),
                  text, chapter_no=no_by_chapter.get(cid, 0), meta={"chapter_id": cid})

    if not chapter_id:                      # 全书级素材只在全量重建时装
        for ch in (await repo.list_characters_by_book(book_id)):
            _push(rows, book_id, "character", str(ch.get("id") or ""),
                  str(ch.get("name") or ""), _char_text(ch), ts=_ms(ch.get("updated_at")))
        for ws in (await repo.list_world_states(book_id)):
            text = (str(ws.get("kind") or "") + " " + str(ws.get("name") or "")
                    + "：" + str(ws.get("value") or "")).strip()
            _push(rows, book_id, "world", str(ws.get("id") or ""), str(ws.get("name") or ""),
                  text, chapter_no=int(ws.get("scene_no") or 0),
                  meta={"kind": ws.get("kind") or ""})
        for fs in (await repo.list_foreshadows(book_id)):
            _push(rows, book_id, "foreshadow", str(fs.get("id") or ""),
                  "伏笔（" + str(fs.get("status") or "") + "）",
                  str(fs.get("text") or ""), meta={"status": fs.get("status") or ""})
        for b in (await repo.list_beliefs(book_id)):
            _push(rows, book_id, "belief", str(b.get("id") or ""),
                  "信念（" + str(b.get("channel") or "") + "）", str(b.get("text") or ""),
                  meta={"channel": b.get("channel") or "", "char_id": b.get("char_id") or ""})
        try:
            mems = await repo.list_memories(book_id, None, 100)
        except Exception:  # noqa: BLE001  记忆表异常不影响其它素材
            mems = []
        for m in (mems or []):
            _push(rows, book_id, "memory", str(m.get("id") or ""),
                  str(m.get("topic") or "memory"), str(m.get("content") or ""),
                  ts=int(m.get("ts") or 0))
    return rows


async def reindex_book(repo, book_id: str) -> dict[str, Any]:
    """全量重建一本书的记忆索引；返回统计（写进 arq 任务 result 给前端看）。"""
    t0 = time.time()
    rows = await build_rows(repo, book_id)
    if not rows:
        return {"book_id": book_id, "chunks": 0, "vectors": 0, "mode": "empty",
                "ms": int((time.time() - t0) * 1000)}
    vectors = 0
    if embedder.available():
        vecs = await embedder.aembed([r["text"] for r in rows])
        if vecs and len(vecs) == len(rows):
            for r, v in zip(rows, vecs):
                r["embedding"] = v
            vectors = len(vecs)
    written = await store.replace_book(book_id, rows)
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    out = {"book_id": book_id, "chunks": written, "vectors": vectors,
           "by_kind": by_kind, "mode": "hybrid" if vectors else "keyword",
           "ms": int((time.time() - t0) * 1000)}
    logger.info("[rag] 重建完成 %s：%s", book_id, out)
    return out
