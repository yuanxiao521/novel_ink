"""seed：把 betrayal_night() 静态内容灌成 books/chapters/scenes/characters 记录。

幂等：books 已存在则跳过。运行入口（backend/ 下）：
  uv run python -m app.db.seed
"""
from __future__ import annotations

import asyncio
import json

from app.db.repo import Repo
from app.scenarios.betrayal_night import betrayal_night

BOOK_ID = "book-rain"
CHAPTER_ID = "chapter-01"
SCENE_ID = "scene-betrayal-night"


async def seed() -> None:
    repo = Repo()
    await repo.init_schema()

    existing = await repo.list_books()
    if any(b["id"] == BOOK_ID for b in existing):
        print(f"[seed] 已存在 {BOOK_ID}，跳过（幂等）。")
        return

    spec = betrayal_night()

    # 书籍
    await repo.save_book({
        "id": BOOK_ID, "title": "雨夜书房", "genre": "悬疑 · 中篇",
        "status": "writing", "cover_init": "雨", "chapter_count": 1,
    })
    # 章节
    await repo.save_chapter({
        "id": CHAPTER_ID, "book_id": BOOK_ID, "title": "第一章 书房夜谈",
        "summary": "陈默雨夜归家，与李文在书房对峙，周婶送茶撞见。", "order_no": 1,
    })
    # 场景：初始事实 + 导演 PlanCfg JSON
    initial_facts = [
        {"id": f.id, "text": f.text, "kind": f.kind,
         "visible_to": f.visible_to, "created_event_id": f.created_event_id, "active": f.active}
        for f in spec["initial_facts"]
    ]
    cfg = spec["plan_cfg"]
    plan_cfg = {
        "secret_fact_ids": list(cfg.secret_fact_ids),
        "expose_target": dict(cfg.expose_target),
        "goal_pressure": [dict(p) for p in cfg.goal_pressure],
        "env_pressure_lines": list(cfg.env_pressure_lines),
        "raise_after_turn": cfg.raise_after_turn,
        "ending_options": list(cfg.ending_options),
    }
    await repo.save_scene({
        "id": SCENE_ID, "chapter_id": CHAPTER_ID, "title": "书房夜谈 · 雨",
        "scenario_def": "betrayal_night",
        "initial_facts_json": json.dumps(initial_facts, ensure_ascii=False),
        "plan_cfg_json": json.dumps(plan_cfg, ensure_ascii=False),
        "cursor_pos": 0,
    })
    # 角色卡（一等实体，含④层字段）
    for cid, card in spec["characters"].items():
        await repo.save_character({
            "id": cid, "scene_id": SCENE_ID, "name": card.name,
            "spec_json": card.model_dump_json(),
        })

    print(f"[seed] 完成：{BOOK_ID} → {CHAPTER_ID} → {SCENE_ID}（{len(spec['characters'])} 角色）")


if __name__ == "__main__":
    asyncio.run(seed())