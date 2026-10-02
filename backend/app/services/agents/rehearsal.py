"""彩排决策（C 批）：主笔**建议** / 责编**请求** / 作者**开关**——三方都能发起，落点一致。

本模块只做 **0-token 的重头戏判定**（给建议，不替作者决定）：
  · 章基调属于张力/揭秘/高潮
  · 场景标题/目标/内容含重头戏关键词
  · 收束章（结局戏）
  · 本场关联多条待回收伏笔
建议显示在骨架行，作者一眼可覆盖；已排演过的场景不再建议（避免重复花钱）。
"""
from __future__ import annotations

CLIMAX_TONES = {"tension", "climax", "reveal", "结算"}
KEYWORDS = (
    "对峙", "决裂", "摊牌", "决斗", "对决", "追杀", "反目", "揭穿", "背叛",
    "抉择", "生死", "转折", "高潮", "真相", "收束", "围攻", "密室", "认亲",
)
SUGGEST_THRESHOLD = 2


def suggest_for_scene(
    scene: dict,
    chapter: dict,
    *,
    is_last_chapter: bool = False,
    rehearsed_turns: int = 0,
    open_foreshadows: int = 0,
) -> dict:
    """返回 {suggested, score, reasons}（纯函数，可单测）。"""
    score = 0
    reasons: list[str] = []

    tone = str(chapter.get("tone") or "")
    if tone in CLIMAX_TONES:
        score += 2
        reasons.append(f"章基调「{tone}」属于重头戏")

    text = f"{scene.get('title') or ''} {scene.get('goal') or ''} {scene.get('content_desc') or ''}"
    hits = [k for k in KEYWORDS if k in text]
    if hits:
        score += 2
        reasons.append("场景含重头戏关键词：" + "、".join(hits[:3]))

    if is_last_chapter:
        score += 1
        reasons.append("收束章：结局戏值得先演一遍")

    if open_foreshadows >= 2:
        score += 1
        reasons.append(f"本场关联 {open_foreshadows} 条待回收伏笔")

    if rehearsed_turns > 0:
        reasons.append(f"已排演 {rehearsed_turns} 回合（可去剧本台看）")

    suggested = score >= SUGGEST_THRESHOLD and rehearsed_turns == 0
    return {"suggested": suggested, "score": score, "reasons": reasons}


async def build_plan(repo, book_id: str, archives_by_scene: dict, foreshadows: list[dict]) -> dict:
    """全书彩排建议（0-token 派生，不新建表）。"""
    tree = await repo.get_book_tree(book_id) or {}
    chapters = sorted(tree.get("chapters") or [], key=lambda c: c.get("order_no") or 0)

    open_fs = [f for f in foreshadows if (f.get("status") or "") not in ("resolved", "collected", "closed")]
    scenes_out: list[dict] = []
    for ci, ch in enumerate(chapters):
        for scene in ch.get("scenes") or []:
            turns = len(archives_by_scene.get(scene.get("id", "")) or [])
            # 本场关联的待回收伏笔：按场景标题/目标做包含匹配（启发式，够用且可解释）
            blob = f"{scene.get('title') or ''} {scene.get('goal') or ''}"
            related = [f for f in open_fs if any(
                kw and kw in blob for kw in [_kw(f.get("text"))]
            )]
            s = suggest_for_scene(
                scene, ch,
                is_last_chapter=ci == len(chapters) - 1,
                rehearsed_turns=turns,
                open_foreshadows=len(related),
            )
            scenes_out.append({
                "scene_id": scene.get("id", ""),
                "scene_title": scene.get("title", ""),
                "chapter_id": ch.get("id", ""),
                "chapter_title": ch.get("title", ""),
                "chapter_no": ci + 1,
                "rehearsed_turns": turns,
                "suggested": s["suggested"],
                "score": s["score"],
                "reasons": s["reasons"],
            })

    return {
        "book_id": book_id,
        "method": "0-token 规则（章基调 / 关键词 / 收束章 / 待回收伏笔）",
        "threshold": SUGGEST_THRESHOLD,
        "scenes": scenes_out,
        "summary": {
            "scenes": len(scenes_out),
            "suggested": sum(1 for s in scenes_out if s["suggested"]),
            "rehearsed": sum(1 for s in scenes_out if s["rehearsed_turns"] > 0),
        },
    }


def _kw(text: object) -> str:
    t = str(text or "").strip()
    return t[:4] if len(t) >= 4 else t
