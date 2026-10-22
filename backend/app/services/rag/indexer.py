"""记忆索引器（v1.49 起，v1.62 升级为「字段级」）：把可检索素材切成记忆块。

索引哪些 kind（对应产品里的「主要业务」）：
  chapter_fact       章 digest 的事实面（事件/状态迁移/新确立设定） → 「前情提要」
  chapter_knowledge  章 digest 的知晓面（谁在哪一章知道了什么）    → 「谁知道什么」（防泄漏）
  chapter_hook       章 digest 的悬念面（章末钩子 + 未解问题）      → 「还欠读者什么」
  chapter           章标题 + 章摘要 + 正文结尾 500 字              → 兜底（没 digest 的章）
  volume            卷级 digest（弧线/人物当前状态/规则/转折/卷末状态）→ 「上一卷收在哪」
  scene             场标题 + 舞台 + 目标 + 内容描述 + 场摘要        → 「上一场发生了什么」
  character         角色卡（spec_json 全字段摊平）                 → 「相关角色（腔调/身份/秘密）」
  world             世界状态账本（kind/name/value）                → 「世界观 / 当前世界状态」
  foreshadow        伏笔三态（buried/in_progress/closed）          → 「未回收伏笔」
  foreshadow_trace  伏笔在某一章被推进的那一次（来自章 digest）     → 「这条伏笔活到哪了」
  belief            角色信念（只索引 text）                        → 「角色以为的事」
  memory            书级记忆（direction/setting/constraint）       → 「作者定过的方向与约束」

为什么 v1.62 要把「章」拆成三块（而不是一大块章摘要）：
  写手要的从来不是"第 3 章的梗概"，而是**某一类事实**——现在是什么状态、谁知道了、
  还欠读者什么。混在一段里检索只能整段搬，预算全花在无关叙述上；拆成字段后
  可以按意图分开召回（见 recall.py 的五个段），精确度上升而注入反而更省。
  没有 digest 的章仍走老的 chapter 块 —— 升级不要求先跑压缩（功能不退化）。

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
# v1.62：kind 名统一在 kinds.py 声明（索引 / 存储 / 召回共用一套，别各写一份字符串）
from app.services.rag.kinds import (
    DIGEST_FACT_KIND, DIGEST_HOOK_KIND, DIGEST_KNOWLEDGE_KIND, FORESHADOW_TRACE_KIND,
    VOLUME_KIND,
)

logger = logging.getLogger(__name__)

MAX_CHAPTER_TAIL = 500      # 章正文只索引结尾：承接要的是"写到哪了"，不是全文
MAX_ENTITY_CHARS = 600      # 单条素材过长就切块（角色卡/世界状态一般远小于此）

# v1.62：章级 digest 拆出来的三类「字段级」素材（kind 常量见 kinds.py）
_SECTION_LABEL = {"fact": "事实", "knowledge": "知晓", "hook": "钩子"}
_ACT_LABEL = {"bury": "埋", "advance": "推进", "close": "回收"}


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


# ---------------------------------------------------------------- 章 digest → 字段级素材（v1.62）
def _digest_obj(ch: dict) -> dict:
    """章 dict 里的 digest_json → dict。坏数据一律当"没有 digest"，绝不抛。"""
    raw = ch.get("digest_json")
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw or "{}"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _digest_fact_text(d: dict) -> str:
    """事实面：一句话结果 + 可拍事件 + 状态迁移 + 新确立设定 + 新出现实体。"""
    parts: list[str] = []
    if str(d.get("one_line") or "").strip():
        parts.append(str(d["one_line"]).strip())
    events = [e for e in (d.get("events") or []) if isinstance(e, dict) and str(e.get("did") or "").strip()]
    if events:
        parts.append("事件：" + "；".join(
            str(e.get("who") or "") + str(e["did"])
            + ("（" + str(e["where"]) + "）" if e.get("where") else "")
            + ("→" + str(e["consequence"]) if e.get("consequence") else "")
            for e in events))
    states = [st for st in (d.get("state_changes") or [])
              if isinstance(st, dict) and str(st.get("target") or "").strip()]
    if states:
        parts.append("状态：" + "；".join(
            str(st["target"]) + " " + str(st.get("from") or "?") + "→" + str(st.get("to") or "?")
            for st in states))
    facts = [str(x).strip() for x in (d.get("facts_established") or []) if str(x).strip()]
    if facts:
        parts.append("新确立：" + "；".join(facts))
    ents = [n for n in (d.get("new_entities") or []) if isinstance(n, dict) and str(n.get("name") or "").strip()]
    if ents:
        parts.append("新出现：" + "；".join(
            str(n["name"]) + "（" + str(n.get("kind") or "实体") + "）"
            + ("：" + str(n["note"]) if n.get("note") else "") for n in ents))
    return "\n".join(p for p in parts if p)


def _digest_knowledge_text(d: dict) -> str:
    """知晓面：谁在这一章知道了什么（后文写他不知情就是硬伤，所以单独成块）。"""
    items = [k for k in (d.get("knowledge") or [])
             if isinstance(k, dict) and str(k.get("who") or "").strip()]
    if not items:
        return ""
    return "；".join(
        str(k["who"]) + "知道了「" + str(k.get("learned") or "") + "」"
        + ("（" + str(k["how"]) + "）" if k.get("how") else "") for k in items)


def _digest_hook_text(d: dict) -> str:
    """悬念面：章末钩子 + 未解问题。"""
    parts: list[str] = []
    if str(d.get("hook") or "").strip():
        parts.append("章末钩子：" + str(d["hook"]).strip())
    opens = [str(x).strip() for x in (d.get("open_questions") or []) if str(x).strip()]
    if opens:
        parts.append("未解问题：" + "；".join(opens))
    return "\n".join(parts)


def _push_chapter_digest(rows: list[dict], book_id: str, ch: dict, no: int, label: str,
                         *, volume_no: int = 0, ts: int = 0) -> bool:
    """章 digest → 事实/知晓/钩子三块。返回 False = 这章没有 digest（调用方保留老 chapter 块）。"""
    d = _digest_obj(ch)
    if not (str(d.get("one_line") or "").strip() or d.get("events")):
        return False
    ref = str(ch.get("id") or "")
    base = {"chapter_no": int(no or 0), "volume_no": int(volume_no or 0)}
    pushed = False
    for kind, section, text in (
        (DIGEST_FACT_KIND, "fact", _digest_fact_text(d)),
        (DIGEST_KNOWLEDGE_KIND, "knowledge", _digest_knowledge_text(d)),
        (DIGEST_HOOK_KIND, "hook", _digest_hook_text(d)),
    ):
        if not text.strip():
            continue
        _push(rows, book_id, kind, ref + "#" + section, label + "·" + _SECTION_LABEL[section],
              text, chapter_no=no, ts=ts, meta={**base, "section": section})
        pushed = True
    return pushed


def _push_foreshadow_traces(rows: list[dict], book_id: str, ch: dict, no: int, ts: int = 0) -> None:
    """章 digest 里"这条伏笔被推动了"的痕迹 → 独立小块（账本只有三态，没有"哪章推的"）。"""
    d = _digest_obj(ch)
    items = [f for f in (d.get("foreshadow_touched") or [])
             if isinstance(f, dict) and str(f.get("text") or "").strip()]
    for i, f in enumerate(items):
        act = str(f.get("action") or "advance").lower()
        _push(rows, book_id, FORESHADOW_TRACE_KIND, str(ch.get("id") or "") + "#fs" + str(i),
              "伏笔推进 · 第 " + str(no) + " 章 " + _ACT_LABEL.get(act, act),
              str(f["text"]).strip(), chapter_no=no, ts=ts,
              meta={"chapter_no": int(no or 0), "action": act})


def _volume_text(d: dict) -> str:
    """卷级 digest → 一块人话素材（人物当前状态是重点：知道/不知道/手里有什么）。"""
    parts: list[str] = []
    if str(d.get("arc") or "").strip():
        parts.append("本卷弧线：" + str(d["arc"]).strip())
    chars = [c for c in (d.get("characters") or [])
             if isinstance(c, dict) and str(c.get("name") or "").strip()]
    if chars:
        lines: list[str] = []
        for c in chars:
            seg = str(c["name"])
            if c.get("where"):
                seg += "在" + str(c["where"])
            if c.get("condition"):
                seg += "，" + str(c["condition"])
            for key, verb in (("holds", "持有"), ("knows", "知道"), ("does_not_know", "不知道")):
                vals = [str(x).strip() for x in (c.get(key) or []) if str(x).strip()]
                if vals:
                    seg += "，" + verb + "、".join(vals)
            if c.get("stance"):
                seg += "，立场：" + str(c["stance"])
            lines.append(seg)
        parts.append("人物当前状态：" + "；".join(lines))
    rules = [str(x).strip() for x in (d.get("rules") or []) if str(x).strip()]
    if rules:
        parts.append("已确立规则：" + "；".join(rules))
    tps = [str(x).strip() for x in (d.get("turning_points") or []) if str(x).strip()]
    if tps:
        parts.append("关键转折：" + "；".join(tps))
    if str(d.get("ending_state") or "").strip():
        parts.append("卷末状态：" + str(d["ending_state"]).strip())
    return "\n".join(p for p in parts if p)


async def _push_volumes(rows: list[dict], repo, book_id: str) -> None:
    """卷级 digest → 一块「上一卷收在哪」。

    chapter_no 取**卷末章号**：卷没写完就不该进当前章的前情（否则会提前泄漏后文状态）。
    stale 的卷概要不索引：边界变过、内容还是旧的，宁缺勿错。
    """
    try:
        digests = await repo.list_book_digests(book_id, "volume")
        volumes = await repo.list_book_volumes(book_id)
    except Exception as e:  # noqa: BLE001  卷概要取不到不影响其它素材
        logger.warning("[rag] 卷级概要取数失败 book=%s：%s", book_id, e)
        return
    no_by_from = {int(v.get("range_from") or 0): int(v.get("no") or 0) for v in (volumes or [])}
    title_by_from = {int(v.get("range_from") or 0): str(v.get("title") or "") for v in (volumes or [])}
    for row in digests or []:
        if row.get("stale"):
            continue
        try:
            data = json.loads(str(row.get("digest_json") or "{}"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        text = _volume_text(data)
        if not text.strip():
            continue
        frm, to = int(row.get("range_from") or 0), int(row.get("range_to") or 0)
        if frm <= 0 or to < frm:
            continue
        no = no_by_from.get(frm, int(data.get("volume_no") or 0))
        title = (("第 " + str(no) + " 卷 " + title_by_from.get(frm, "")).strip()
                 if no else ("第 " + str(frm) + "~" + str(to) + " 章概要"))
        _push(rows, book_id, VOLUME_KIND, str(row.get("id") or ""), title, text,
              chapter_no=to, ts=_ms(row.get("updated_at")),
              meta={"volume_no": no, "range": [frm, to], "section": "volume"})


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
    # v1.62：章 → 卷号（digest 素材的 meta 要能说清"哪一卷"，卷级素材自己也要用）
    vol_no_by_id: dict[str, int] = {}
    try:
        vol_no_by_id = {str(v.get("id")): int(v.get("no") or 0)
                        for v in (await repo.list_book_volumes(book_id))}
    except Exception:  # noqa: BLE001  卷表读不到不影响章级素材
        vol_no_by_id = {}

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
        # v1.62 · 字段级：这一章的 digest 拆成「事实 / 知晓 / 钩子」三块（没有就只留上面那块）
        try:
            vol_no = vol_no_by_id.get(str(ch.get("volume_id") or ""), 0)
            _push_chapter_digest(rows, book_id, ch, no, title, volume_no=vol_no,
                                 ts=_ms(ch.get("updated_at")))
            _push_foreshadow_traces(rows, book_id, ch, no, ts=_ms(ch.get("updated_at")))
        except Exception as e:  # noqa: BLE001  坏 digest 不能拖垮整次重建
            logger.warning("[rag] 章 digest 索引失败 chapter=%s：%s", ch.get("id"), e)

    for ch in chapters:
        cid = str(ch.get("id") or "")
        if chapter_id and cid != chapter_id:
            continue
        # v1.62 修：DB 模式的 list_chapters_by_book **不带 scenes**（只有内存态的对象里才有），
        # 所以线上 mem_chunks 里 scene 一类从来没进过索引（实测 5 个场 → 0 个 scene 块）。
        scenes = ch.get("scenes")
        if scenes is None:
            try:
                scenes = await repo.list_scenes_by_chapter(cid)
            except Exception as e:  # noqa: BLE001  取不到场不影响其它素材
                logger.warning("[rag] 场素材取数失败 chapter=%s：%s", cid, e)
                scenes = []
        for sc in (scenes or []):
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
        # v1.62：卷级 digest（一个已写完的卷 → 一块「上一卷收在哪」）
        await _push_volumes(rows, repo, book_id)
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
