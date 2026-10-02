"""统一感知出口：perceive(scope) → PerceptPacket（B 批）。

设计依据：docs/正文协作副驾与Agent协作架构.md §2.2/§6。
契约：**结构化**（可断言"它到底看到了什么"）+ **裁剪留痕**（dropped 带原因）+ token 预算。
同一份包可由不同 persona 渲染成不同 prompt（见 context.render）。

scope：
  character_view —— 角色视角隔离。**现状即参照实现**（engine/character.perceive_context 读运行时 sim，
                    不经 repo），这里只登记不搬动，避免行为变化。
  scene          —— 责编：本场 + 约束 + 批注 + 审计（写作侧信号的雏形）
  book           —— 主笔：书树概览 + 记忆 + 约束 + 写作侧信号
  task           —— 编排者：任务上下文（最小）
"""
from __future__ import annotations

from app.services.agents import constraints as constraints_mod

SCENE_LIMIT = 12      # 批注/审计进包的条数上限（超出的写进 dropped）


async def _scene_packet(repo, scene_id: str) -> dict:
    scene = await repo.get_scene(scene_id) or {}
    annotations = await repo.list_annotations(scene_id, status="open")
    notes = await repo.list_prose_notes(scene_id, limit=30)
    characters = await repo.list_characters_by_scene(scene_id)
    prose = scene.get("final_prose") or ""

    dropped: list[dict] = []
    if not scene:
        dropped.append({"src": "scene", "reason": "场景不存在或读取失败"})
    if not characters:
        dropped.append({"src": "characters", "reason": "本场未配置上场角色（可在人物页生成角色卡）"})
    if len(annotations) > SCENE_LIMIT:
        dropped.append({"src": "annotations", "reason": f"超出 {SCENE_LIMIT} 条，仅保留最新的待处理批注"})
    if len(notes) > SCENE_LIMIT:
        dropped.append({"src": "recent_notes", "reason": f"超出 {SCENE_LIMIT} 条，仅保留最近审计"})

    chapter_id = scene.get("chapter_id") or ""
    book_id = ""
    if chapter_id:
        ch = await repo.get_chapter(chapter_id)
        book_id = (ch or {}).get("book_id", "")
    table = await constraints_mod.build_constraints(repo, book_id)
    cblock = constraints_mod.as_block(table)
    dropped.extend([{**d, "src": f"constraints:{d['src']}"} for d in cblock["dropped"]])

    char_items = []
    for c in characters[:8]:
        spec = c.get("spec") or {}
        char_items.append({"name": c.get("name", ""), "summary": spec.get("summary", ""), "voice": spec.get("voice", "")})

    return {
        "who": {"id": "editor", "name": "责编"},
        "scope": "scene",
        "scene_id": scene_id,
        "book_id": book_id,
        "sources": ["scene", "constraints", "characters", "annotations", "prose_notes"],
        "budget": {"max_tokens": 1400, "used": None},
        "blocks": [
            {"kind": "scene", "visibility": "own", "items": [{
                "title": scene.get("title", ""), "goal": scene.get("goal", ""),
                "stage_desc": scene.get("stage_desc", ""), "content_desc": scene.get("content_desc", ""),
                "prose_chars": len(prose),
            }]},
            cblock,
            {"kind": "characters", "visibility": "book", "items": char_items},
            {"kind": "annotations", "visibility": "own", "items": [
                {"id": a["id"], "para_index": a.get("para_index"), "quote": a.get("quote", ""), "note": a.get("note", "")}
                for a in annotations[:SCENE_LIMIT]
            ]},
            {"kind": "draft", "visibility": "own", "items": [{"chars": len(prose), "text": prose[:4000]}]},
            {"kind": "recent_notes", "window": f"last{SCENE_LIMIT}", "items": [
                {"kind": n.get("kind"), "status": n.get("status"), "suggestion": (n.get("suggestion") or "")[:120]}
                for n in notes[:SCENE_LIMIT]
            ]},
        ],
        "dropped": dropped,
    }


async def _book_packet(repo, book_id: str) -> dict:
    tree = await repo.get_book_tree(book_id) or {}
    memories = await repo.list_memories(book_id) or []
    table = await constraints_mod.build_constraints(repo, book_id)

    chapters = tree.get("chapters") or []
    scenes = [s for c in chapters for s in (c.get("scenes") or [])]
    prose_chars = sum(len(s.get("final_prose") or "") for s in scenes)
    annotations = 0
    for s in scenes:
        annotations += len(await repo.list_annotations(s.get("id", ""), status="open"))

    dropped: list[dict] = []
    if not chapters:
        dropped.append({"src": "outline", "reason": "还没有章节骨架（让主笔排骨架）"})
    if not memories:
        dropped.append({"src": "memories", "reason": "没有书级记忆"})
    dropped.extend(table.get("dropped", []))

    return {
        "who": {"id": "chief", "name": "主笔"},
        "scope": "book",
        "book_id": book_id,
        "sources": ["book_tree", "memories", "constraints", "writing_signals"],
        "budget": {"max_tokens": 1600, "used": None},
        "blocks": [
            {"kind": "book", "visibility": "book", "items": [{
                "title": tree.get("title", ""), "genre": tree.get("genre", ""),
                "chapters": len(chapters), "scenes": len(scenes), "prose_chars": prose_chars,
            }]},
            constraints_mod.as_block(table),
            {"kind": "outline", "visibility": "book", "items": [
                {"no": i + 1, "title": c.get("title", ""), "tone": c.get("tone", ""),
                 "scenes": len(c.get("scenes") or []),
                 "written": sum(1 for s in (c.get("scenes") or []) if (s.get("final_prose") or "").strip())}
                for i, c in enumerate(chapters)
            ]},
            {"kind": "memories", "visibility": "book", "items": [
                {"topic": m.get("topic"), "content": (m.get("content") or "")[:200]} for m in memories[:20]
            ]},
            # 写作侧信号（0-token 派生 · 上行回主笔的第一个雏形）
            {"kind": "writing_signals", "visibility": "book", "items": [{
                "prose_chars": prose_chars,
                "annotation_open": annotations,
                "scenes_total": len(scenes),
                "scenes_written": sum(1 for s in scenes if (s.get("final_prose") or "").strip()),
            }]},
        ],
        "dropped": dropped,
    }


async def perceive(repo, scope: str, *, scene_id: str = "", book_id: str = "", ctx: dict | None = None) -> dict:
    """统一入口。未知 scope → 明确报错（不静默返回空包）。"""
    if scope == "scene":
        return await _scene_packet(repo, scene_id)
    if scope == "book":
        return await _book_packet(repo, book_id)
    if scope == "task":
        return {
            "who": {"id": "orchestrator", "name": "编排者"},
            "scope": "task", "sources": ["task"],
            "budget": {"max_tokens": 400, "used": None},
            "blocks": [{"kind": "tasks", "visibility": "task", "items": list((ctx or {}).get("tasks") or [])}],
            "dropped": [],
        }
    if scope == "character_view":
        # 参照实现：engine/character.perceive_context（读运行时 sim，保持不动）
        return {
            "who": {"id": "character", "name": "角色"},
            "scope": "character_view", "sources": ["card", "beliefs", "facts", "events"],
            "budget": {"max_tokens": 900, "used": None},
            "blocks": [{"kind": "note", "visibility": "own", "items": [
                {"text": "角色视角由 engine/character.perceive_context 提供（视角隔离），本出口只做登记"}
            ]}],
            "dropped": [],
        }
    raise ValueError(f"未知 scope：{scope}（可选 scene/book/task/character_view）")


def block(packet: dict, kind: str) -> dict:
    for b in packet.get("blocks", []):
        if b.get("kind") == kind:
            return b
    return {}


def draft_text(packet: dict) -> str:
    items = block(packet, "draft").get("items") or [{}]
    return str(items[0].get("text") or "")


def packet_summary(packet: dict) -> dict:
    counts = {b["kind"]: len(b.get("items") or []) for b in packet.get("blocks", [])}
    return {
        "who": packet.get("who", {}), "scope": packet.get("scope", ""),
        "scene_id": packet.get("scene_id", ""), "book_id": packet.get("book_id", ""),
        "sources": packet.get("sources", []), "counts": counts,
        "dropped": packet.get("dropped", []),
    }
