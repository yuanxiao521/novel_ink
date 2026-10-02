"""书级约束表（B 批）：主笔维护、**全员读**。0-token 派生，**不新建表**。

"主笔掌管一切"的落地形态 = 主笔掌管**约束**，而约束对全部 agent 生效。
数据源（都已存在）：books.worldview_json / world_rules_json + book_memories(direction|constraint|preference|setting)
"""
from __future__ import annotations

import json

MEMORY_TOPIC_LABEL = {
    "direction": "方向", "setting": "设定", "constraint": "约束",
    "history": "历史", "preference": "偏好",
}


def _load(raw: object, default: object) -> object:
    if raw is None:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return default


async def build_constraints(repo, book_id: str) -> dict:
    """派生约束表（可断言、可展示、可注入 prompt）。"""
    if not book_id:
        return {"book_id": "", "direction": "", "worldview": {}, "hard_rules": [],
                "constraints": [], "preferences": [], "settings": [], "counts": {},
                "dropped": [{"src": "book", "reason": "未指定书"}]}

    book = await repo.get_book(book_id) or {}
    memories = await repo.list_memories(book_id) or []

    def _by(topic: str) -> list[str]:
        return [str(m.get("content") or "") for m in memories if m.get("topic") == topic and m.get("content")]

    direction = (_by("direction") or [""])[0]
    worldview = _load(book.get("worldview_json"), {})
    if not isinstance(worldview, dict):
        worldview = {}
    hard_rules = _load(book.get("world_rules_json"), [])
    if not isinstance(hard_rules, list):
        hard_rules = []

    dropped: list[dict] = []
    if not direction:
        dropped.append({"src": "direction", "reason": "未填写方向（设定页「方向」可补）"})
    if not worldview:
        dropped.append({"src": "worldview", "reason": "还没有世界观（让主笔构思骨架后自动写入）"})
    if not hard_rules:
        dropped.append({"src": "world_rules", "reason": "没有硬规则（0-token 护栏无规则可执行）"})
    if not _by("constraint"):
        dropped.append({"src": "constraint", "reason": "没有写作约束（设定页「约束」可补）"})

    return {
        "book_id": book_id,
        "source": "derived",           # 派生：不是新表，随时可重建
        "direction": direction,
        "worldview": worldview,
        "hard_rules": [
            {"concept": str(r.get("concept") or ""), "constraint": str(r.get("constraint") or ""),
             "keywords": [str(k) for k in (r.get("keywords") or [])]}
            for r in hard_rules if isinstance(r, dict)
        ],
        "constraints": _by("constraint"),
        "preferences": _by("preference"),
        "settings": _by("setting"),
        "counts": {
            "direction": 1 if direction else 0,
            "hard_rules": len(hard_rules),
            "constraints": len(_by("constraint")),
            "preferences": len(_by("preference")),
            "settings": len(_by("setting")),
        },
        "dropped": dropped,
    }


def as_block(table: dict) -> dict:
    """转成 PerceptPacket 的 constraints 块（全员读同一份）。"""
    return {
        "kind": "constraints",
        "visibility": "book",     # 书级：所有 agent 都读
        "items": [{
            "direction": table.get("direction", ""),
            "worldview_premise": (table.get("worldview") or {}).get("premise", ""),
            "hard_rules": [r["constraint"] for r in table.get("hard_rules", []) if r.get("constraint")],
            "constraints": table.get("constraints", []),
            "preferences": table.get("preferences", []),
        }],
        "dropped": table.get("dropped", []),
    }


def render(table: dict) -> str:
    """约束表的 prompt 片段（写手/责编/主笔共用同一份文本，保证口径一致）。"""
    hard = "；".join(r["constraint"] for r in table.get("hard_rules", []) if r.get("constraint"))
    lines = [
        "【书级约束 · 全员必须遵守】",
        f"- 方向：{table.get('direction') or '（未填写）'}",
        f"- 世界观前提：{(table.get('worldview') or {}).get('premise') or '（未填写）'}",
        f"- 硬规则：{hard or '（无）'}",
        f"- 写作约束：{'；'.join(table.get('constraints') or []) or '（无）'}",
    ]
    return "\n".join(lines)
