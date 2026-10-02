"""素材使用率（C 批）：演了的东西，写的时候到底用上没有。

背景：S4 的涌现素材要"勾选才注入"，但作者看不到"演了 3 条高光、写手用了 1 条"——
于是排演像白花钱。本模块用 **0-token 文本命中法**给第一版使用率（**启发式，可解释**）：
  已采纳素材的标题/标题前 4 字是否出现在本场正文里 → 记为"引用"。
不追求精确语义匹配；目的是给作者一个"值不值得继续演"的信号。
"""
from __future__ import annotations

import re

_PUNCT = re.compile(r"[《》「」【】\[\]()（）:：,，。.、!！?？\-—_/\s]")


def _candidates(card: dict) -> list[str]:
    title = _PUNCT.sub("", str(card.get("title") or ""))
    out = []
    if len(title) >= 2:
        out.append(title)
    if len(title) >= 4:
        out.append(title[:4])
    return list(dict.fromkeys(out))


async def build_usage(repo, book_id: str, archives_by_scene: dict | None = None) -> dict:
    """每场：已采纳素材数 / 正文里命中的数（启发式）。"""
    tree = await repo.get_book_tree(book_id) or {}
    cards = await repo.list_inspirations(book_id) or []
    adopted = [c for c in cards if c.get("adopted")]

    scenes_out: list[dict] = []
    for ch in tree.get("chapters") or []:
        for scene in ch.get("scenes") or []:
            prose = scene.get("final_prose") or ""
            items = []
            for c in adopted:
                hit = next((k for k in _candidates(c) if k and k in prose), "")
                items.append({
                    "id": c.get("id", ""), "title": c.get("title", ""),
                    "referenced": bool(hit), "match": hit,
                })
            referenced = sum(1 for i in items if i["referenced"])
            scenes_out.append({
                "scene_id": scene.get("id", ""),
                "scene_title": scene.get("title", ""),
                "prose_chars": len(re.sub(r"\s", "", prose)),
                "adopted": len(adopted),
                "referenced": referenced,
                "items": items,
            })

    total_adopted = sum(s["adopted"] for s in scenes_out)
    total_ref = sum(s["referenced"] for s in scenes_out)
    return {
        "book_id": book_id,
        "method": "文本命中法（标题/标题前 4 字出现在正文里即记引用）· 启发式",
        "adopted_cards": [{"id": c.get("id"), "title": c.get("title")} for c in adopted],
        "scenes": scenes_out,
        "summary": {
            "adopted": len(adopted),
            "referenced_slots": total_ref,
            "slot_total": total_adopted,
            "rate": round(total_ref / total_adopted, 3) if total_adopted else None,
        },
    }
