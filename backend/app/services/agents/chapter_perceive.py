"""章级责编感知包（v1.65）：两个页面合并的底座。

为什么单独一个模块而不是塞进 perceive.py：
  ① 章级包要读**前情 + 记忆检索 + 场清单**（跨 engine/rag），依赖比场景包重；
  ② 合并后「章」是主线单位，它会长得比场景包大 —— 分文件便于单测与后续演进；
  ③ perceive.py 保持"薄路由"，scene/book/task 行为一行不动（不引入回归面）。

与 scene 包的差别（也是合并的核心）：
  - 单位是**章**：章正文（唯一真源 chapters.prose_md）+ 摘要/字数目标/生产方式；
  - 带**前情**（全书 / 本卷 / 最近两章 digest）与**记忆检索五段**（写手会读到的那份）；
  - 带**场清单 + 每场状态**（正文多少字、是否已并入章正文、待处理批注）——场是章的细分，不是另一个文档；
  - 带**角色档位**（主角/关键配角/反派…），责编判断「谁该出现」要靠它。
"""
from __future__ import annotations

import json
from typing import Any

from app.services.agents import constraints as constraints_mod
from app.services.text_metrics import chapter_body, prose_chars

BLOCK_TEXT_LIMIT = 1200      # 前情/记忆块进 prompt 的上限（避免把正文挤掉）
BODY_LIMIT = 6000            # 章正文进 prompt 的上限（责编要看全文时按需可见更多）


def _clip(val: object, limit: int) -> str:
    text = " ".join(str(val or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _digest_json(row: dict | None) -> dict:
    """章/卷 digest 行（dict 里是 digest_json 字符串）→ dict；坏数据当空。"""
    if not row:
        return {}
    raw = row.get("digest_json") if isinstance(row, dict) else None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw or "{}"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


async def _prior_text(repo, book_id: str, chapter: dict, chapters: list[dict]) -> tuple[str, list[dict]]:
    """前情（全书 / 本卷 / 最近两章）→ 与写手 prompt 同一份口径。"""
    from app.services.engine.digest import render_prior_bundle

    no = int(chapter.get("order_no") or 0)
    book_digest = await repo.get_book_digest(book_id, "book")
    prev_chapters = [c for c in chapters if int(c.get("order_no") or 0) < no]
    vol_row = None
    for v in await repo.list_book_volumes(book_id):
        if int(v.get("range_from") or 0) <= no <= int(v.get("range_to") or 0):
            vol_row = await repo.get_book_digest(book_id, "volume", int(v.get("range_from") or 0))
            break
    text = render_prior_bundle({
        "book": _digest_json(book_digest),
        "volume": _digest_json(vol_row),
        "recent": [_digest_json(c) for c in prev_chapters[-2:]],
    })
    return text, prev_chapters


async def _recall_text(repo, book_id: str, chapter_id: str) -> tuple[str, dict]:
    """记忆检索（写手会读到的那五段）——与写作页左栏面板同一个接口口径。"""
    from app.services.rag import recall as rag_recall

    bundle = await rag_recall.chapter_recall(repo, book_id, chapter_id)
    return rag_recall.render(bundle), dict(bundle.get("counts") or {})


async def _scene_items(repo, scenes: list[dict], body: str) -> tuple[list[dict], int]:
    """场清单：每场多少字 / 是否已在章正文里 / 待处理批注数。"""
    items: list[dict] = []
    ann_total = 0
    for i, s in enumerate(scenes, start=1):
        sid = str(s.get("id") or "")
        try:
            anns = await repo.list_annotations(sid, status="open")
        except Exception:  # noqa: BLE001
            anns = []
        ann_total += len(anns)
        sp = str(s.get("final_prose") or "").strip()
        items.append({
            "no": i, "id": sid, "title": s.get("title") or "",
            "goal": _clip(s.get("goal"), 60), "stage": _clip(s.get("stage_desc"), 60),
            "prose_chars": prose_chars(sp),
            "in_chapter": bool(sp) and sp in body,
            "open_annotations": len(anns),
        })
    return items, ann_total


async def _char_items(repo, book_id: str) -> list[dict]:
    """角色 + 档位（README：5 档 → 中文标签，与前端 charLabel 同一套）。"""
    from app.services.engine.cast_roles import TIER_LABEL

    out: list[dict] = []
    for c in await repo.list_characters_by_book(book_id):
        tier = str(c.get("role_tier") or "")
        out.append({
            "name": c.get("name") or "",
            "tier": TIER_LABEL.get(tier, tier or "未分档"),
            "summary": _clip((c.get("spec") or {}).get("summary"), 80),
        })
    return out


async def build_chapter_packet(repo, chapter_id: str) -> dict:
    """装配章级感知包（结构同 PerceptPacket：who/scope/sources/blocks/dropped）。"""
    chapter = await repo.get_chapter(chapter_id) or {}
    book_id = str(chapter.get("book_id") or "")
    chapters = (await repo.list_chapters_by_book(book_id)) if book_id else []
    scenes = await repo.list_scenes_by_chapter(chapter_id)
    body = chapter_body(chapter, scenes)

    dropped: list[dict] = []
    if not chapter:
        dropped.append({"src": "chapter", "reason": "章节不存在或读取失败"})
    if not scenes:
        dropped.append({"src": "scenes", "reason": "本章没有拆场（可直接写整章，拆场是可选细分）"})

    prior = ""
    try:
        prior, _prev = await _prior_text(repo, book_id, chapter, chapters)
        if not prior:
            dropped.append({"src": "prior", "reason": "还没有前情概要（正文保存后会自动生成）"})
    except Exception as e:  # noqa: BLE001  前情读不到不影响副驾（dropped 会说明）
        dropped.append({"src": "prior", "reason": "前情概要读取失败：" + type(e).__name__})

    recall = ""
    recall_counts: dict = {}
    try:
        recall, recall_counts = await _recall_text(repo, book_id, chapter_id)
        if not recall:
            dropped.append({"src": "recall", "reason": "本章没有可注入的记忆素材（首章或索引未建）"})
    except Exception as e:  # noqa: BLE001
        dropped.append({"src": "recall", "reason": "记忆检索失败：" + type(e).__name__})

    table = await constraints_mod.build_constraints(repo, book_id)
    cblock = constraints_mod.as_block(table)
    dropped.extend([{**d, "src": "constraints:" + str(d.get("src") or "")} for d in cblock.get("dropped", [])])

    scene_items, ann_total = await _scene_items(repo, scenes, body)
    try:
        chars = await _char_items(repo, book_id)
        if not chars:
            dropped.append({"src": "characters", "reason": "这本书还没有角色卡"})
    except Exception:  # noqa: BLE001
        chars = []
        dropped.append({"src": "characters", "reason": "角色卡读取失败"})

    return {
        "who": {"id": "editor", "name": "责编"},
        "scope": "chapter",
        "chapter_id": chapter_id,
        "book_id": book_id,
        "sources": ["chapter", "scenes", "constraints", "characters", "prior", "recall"],
        "budget": {"max_tokens": 2200, "used": None},
        "blocks": [
            {"kind": "chapter", "visibility": "book", "items": [{
                "no": int(chapter.get("order_no") or 0), "title": chapter.get("title") or "",
                "summary": _clip(chapter.get("summary"), 200), "tone": chapter.get("tone") or "",
                "word_target": int(chapter.get("word_target") or 0),
                "mode": chapter.get("mode") or "draft",
                "prose_chars": prose_chars(body), "chapters_total": len(chapters),
            }]},
            {"kind": "scenes", "visibility": "book", "items": scene_items},
            cblock,
            {"kind": "prior", "visibility": "book", "items": [{"text": prior[:BLOCK_TEXT_LIMIT]}]},
            {"kind": "recall", "visibility": "book",
             "items": [{"counts": recall_counts, "text": recall[:BLOCK_TEXT_LIMIT]}]},
            {"kind": "characters", "visibility": "book", "items": chars[:10]},
            {"kind": "draft", "visibility": "own",
             "items": [{"chars": prose_chars(body), "text": body[:BODY_LIMIT]}]},
        ],
        "dropped": dropped,
        "annotations_open": ann_total,
    }


def summary(packet: dict) -> dict:
    """给前端/审计看的一眼摘要（不含正文全文）。"""
    block_map: dict[str, Any] = {b.get("kind"): b for b in packet.get("blocks", [])}
    ch = ((block_map.get("chapter") or {}).get("items") or [{}])[0]
    return {
        "scope": packet.get("scope", ""),
        "chapter": {k: ch.get(k) for k in ("no", "title", "word_target", "mode", "prose_chars")},
        "scenes": len((block_map.get("scenes") or {}).get("items") or []),
        "annotations_open": int(packet.get("annotations_open") or 0),
        "dropped": packet.get("dropped", []),
    }
