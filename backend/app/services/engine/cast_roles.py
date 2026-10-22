"""角色分层（v1.53 · 基调：涌现只演"能改变局面的人"）。

为什么必须分层：
  涌现的价值在"反应的意外"，不在"人数的众多"。把全书角色都塞进回合，结果是
  ① 群像开会（每人说一句，张力被摊薄）；② 成本线性涨（N 个 agent × 每回合一次 LLM）；
  ③ 主角被淹没（读者记不住谁在推事）。

三层基调（这一层是数据，不是文案）：
  protagonist 主角  → 必须演，可以有多个（双主）
  major       关键配角 → 演；推动主线的少数人
  antagonist  反派  → 演；导演优先对其施压（goal_pressure）
  minor       次要  → **不进回合**，只作"在场背景"喂给写手
  extra       背景  → 同上（更弱，可只给名字）

边界：分层只管"谁进回合"，不管"发生什么"——必然事件（beats）归主笔，
路径与收束归导演，角色只在约束内自由反应。三者别混。
"""
from __future__ import annotations

from typing import Any, Iterable

TIERS: tuple[str, ...] = ("protagonist", "major", "antagonist", "minor", "extra")
TIER_LABEL: dict[str, str] = {
    "protagonist": "主角",
    "major": "关键配角",
    "antagonist": "反派",
    "minor": "次要",
    "extra": "背景",
}
DEFAULT_TIER = "minor"

# 会进回合的档位（顺序 = 选角优先级）
PLAYABLE_TIERS: tuple[str, ...] = ("protagonist", "antagonist", "major")
CAST_LIMIT = 5          # 默认上场人数上限（再多就摊薄张力，且成本线性涨）


def tier_of(character: dict) -> str:
    """取角色档位；缺失/非法一律按 minor（宁可少演，也别误把龙套当主角）。"""
    raw = str(character.get("role_tier") or "").strip()
    return raw if raw in TIERS else DEFAULT_TIER


def is_playable(character: dict) -> bool:
    return tier_of(character) in PLAYABLE_TIERS


def tier_label(character: dict) -> str:
    return TIER_LABEL[tier_of(character)]


def _related_score(name: str, related_text: str) -> int:
    """名字是否出现在本章/本场文本里（0-token 相关性，用来同级排序）。"""
    n = str(name or "").strip()
    if not n or not related_text:
        return 0
    return related_text.count(n)


def split_cast(characters: Iterable[dict], *, related_text: str = "",
               limit: int = CAST_LIMIT) -> dict[str, Any]:
    """把角色分成「进回合」与「只作背景」两组（纯函数，0-token）。

    排序规则：档位优先级（主角 > 反派 > 关键配角）→ 与本章/本场文本的相关度 → 名字（稳定）。
    超出上限的**可演角色降级为背景**，并在 dropped 里如实列出（不静默丢人）。
    """
    playable: list[tuple[int, int, str, dict]] = []
    background: list[dict] = []
    for c in characters or []:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        if not is_playable(c):
            background.append(c)
            continue
        tier = tier_of(c)
        rank = PLAYABLE_TIERS.index(tier)
        playable.append((rank, -_related_score(name, related_text), name, c))

    playable.sort(key=lambda x: (x[0], x[1], x[2]))
    limit = max(1, int(limit or CAST_LIMIT))
    chosen = playable[:limit]
    dropped = playable[limit:]

    cast = [c for *_x, c in chosen]
    for *_x, c in dropped:
        background.append(c)

    notes: list[str] = []
    if dropped:
        notes.append("超出上限（%d 人）：" % limit
                     + "、".join(str(c.get("name") or "") for *_x, c in dropped)
                     + " 不进回合，只作背景")
    if background:
        notes.append("背景角色（%d 位，不进回合）：" % len(background)
                     + "、".join(str(c.get("name") or "") for c in background[:8]))

    return {
        "cast": [str(c.get("id") or "") for c in cast],
        "cast_names": [str(c.get("name") or "") for c in cast],
        "background": [str(c.get("id") or "") for c in background],
        "background_names": [str(c.get("name") or "") for c in background],
        "dropped": [str(c.get("name") or "") for *_x, c in dropped],
        "limit": limit,
        "notes": notes,
    }


def background_block(characters: Iterable[dict], *, limit: int = 8) -> str:
    """背景下压成写手素材：他们"在"，但不抢戏（一句话带过即可）。"""
    names = [str(c.get("name") or "").strip() for c in characters or []
             if tier_of(c) not in PLAYABLE_TIERS]
    names = [n for n in names if n][:limit]
    if not names:
        return ""
    return ("【在场背景角色（可以写他们在场/一个动作，但不要让他们抢戏、不要给重要台词）】\n- "
            + "、".join(names))

# ---------------------------------------------------------------- 存量补档位（一次 LLM）
_INFER_SCHEMA = [{
    "assignments": [{
        "id": "str 角色 id（必须来自给定清单，不要编）",
        "role_tier": "str protagonist/major/antagonist/minor/extra",
        "why": "str 一句话理由（≤20 字）",
    }],
}]

_INFER_PROMPT = """你是小说主笔。给下面这本书的角色卡**分档**（这决定谁进涌现回合）。

档位定义：
- protagonist 主角：推动主线、视角中心（可以有多个，比如双主）
- antagonist 反派：与主角目标正面冲突、要施压的人
- major 关键配角：推动主线的少数人（导师/搭档/关键线人）
- minor 次要：有名字但只服务个别情节
- extra 背景：路人、群众（只出现在场面描写里）

规则：主角 1~2 个（最多 3）；反派 0~2 个；关键配角 2~4 个；其余 minor/extra。
只输出 JSON：{{"assignments": [{{"id": "...", "role_tier": "...", "why": "..."}}]}}，不要解释。

书目信息：{context}

角色清单：
{roster}"""


def clean_tier(value: object) -> str:
    """归一化档位字符串（模型常返回大写/中文，统一收敛）。"""
    raw = str(value or "").strip()
    low = raw.lower()
    if low in TIERS:
        return low
    alias = {"主角": "protagonist", "关键配角": "major", "配角": "major",
             "反派": "antagonist", "次要": "minor", "背景": "extra",
             "protagonist": "protagonist", "major": "major",
             "antagonist": "antagonist", "minor": "minor", "extra": "extra"}
    return alias.get(raw, DEFAULT_TIER)


def normalize_assignments(raw: object, valid_ids: Iterable[str]) -> list[dict]:
    """清洗模型分档结果：只保留清单里的 id、去重、档位归一（宁可漏，不许编）。"""
    if not isinstance(raw, dict):
        return []
    allowed = {str(x) for x in valid_ids}
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw.get("assignments") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if not cid or cid not in allowed or cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "role_tier": clean_tier(item.get("role_tier")),
                    "why": " ".join(str(item.get("why") or "").split())[:24]})
    return out


async def infer_role_tiers(llm, characters: list[dict], *, context: str = "") -> list[dict]:
    """让模型给存量角色卡分档；不可用/失败 → []（调用方如实告知，不静默编）。"""
    from app.services.engine.schema_retry import validate_and_retry

    rows = [c for c in characters or [] if str(c.get("id") or "").strip()]
    if not rows or not getattr(llm, "available", False):
        return []
    roster = "\n".join(
        "- id=%s｜%s｜%s" % (c.get("id"), c.get("name") or "",
                             (str(c.get("summary") or "")[:60] or "（无人设）"))
        for c in rows)
    prompt = _INFER_PROMPT.format(context=context or "（无）", roster=roster)

    def _validate(raw: object) -> str | None:
        if not isinstance(raw, dict):
            return "顶层必须是 JSON 对象"
        items = raw.get("assignments")
        if not isinstance(items, list) or not items:
            return "assignments 必须是非空数组"
        return None

    try:
        raw = await validate_and_retry(llm, prompt, _INFER_SCHEMA, _validate)
    except Exception:  # noqa: BLE001
        return []
    return normalize_assignments(raw, [str(c.get("id")) for c in rows])


def default_cast(characters: list[dict], *, related_text: str = "",
                 limit: int = CAST_LIMIT) -> dict[str, Any]:
    """导演台开场用的默认选角。

    规则（用户拍板）：主角+关键配角+反派，上限 limit；其余不进回合。
    **兜底诚实**：全书都没有分档（存量数据）时，不能给空 cast（导演台会空转），
    改为按"与本章/本场文本相关度"取前 limit 位，并在 notes 里明说"未分档，建议补档位"。
    """
    rows = [c for c in characters or [] if str(c.get("name") or "").strip()]
    if not rows:
        return {"cast": [], "cast_names": [], "background": [], "background_names": [],
                "dropped": [], "limit": limit, "notes": [], "tiered": False}
    split = split_cast(rows, related_text=related_text, limit=limit)
    if split["cast"]:
        split["tiered"] = True
        return split
    fallback = sorted(rows, key=lambda c: (-_related_score(str(c.get("name") or ""), related_text),
                                           str(c.get("name") or "")))[:max(1, int(limit))]
    keep = {str(c.get("id") or "") for c in fallback}
    others = [c for c in rows if str(c.get("id") or "") not in keep]
    return {
        "cast": [str(c.get("id") or "") for c in fallback],
        "cast_names": [str(c.get("name") or "") for c in fallback],
        "background": [str(c.get("id") or "") for c in others],
        "background_names": [str(c.get("name") or "") for c in others],
        "dropped": [],
        "limit": limit,
        "tiered": False,
        "notes": ["角色还没分档，默认先取与前文最相关的 %d 位（建议去人物页补档位）" % len(fallback)],
    }

# ---------------------------------------------------------------- 回合节流（v1.54）
MAX_ACTORS_PER_TURN = 3   # 每回合最多几个人"行动"（其余静默反应，不产生台词与决策）


def throttle_order(roster: Iterable[str], *, tiers: dict[str, str] | None = None,
                   protagonists: Iterable[str] | None = None,
                   priority: Iterable[str] | None = None,
                   limit: int = MAX_ACTORS_PER_TURN) -> list[str]:
    """每回合的行动名单（纯函数，0-token）。

    规则：**主角固定在场**（视角人物，每回合都该有反应），其余按 priority（导演排的主动权顺序）
    补位到 limit 人；不在名单里的角色本回合"静默"——不思考、不发言、不决策。
    为什么必须节流：不节流就是"所有人每回合都演"，既群像开会、张力摊薄，也成本线性涨。
    """
    names = [str(x) for x in (roster or []) if str(x)]
    if not names:
        return []
    limit = max(1, int(limit or MAX_ACTORS_PER_TURN))
    prot = [str(x) for x in (protagonists or []) if str(x) in names]
    chosen: list[str] = prot[:limit]
    pool: list[str] = []
    for c in (priority or []):
        cid = str(c)
        if cid in names and cid not in chosen and cid not in pool:
            pool.append(cid)
    for cid in names:
        if cid not in chosen and cid not in pool:
            pool.append(cid)
    for cid in pool:
        if len(chosen) >= limit:
            break
        chosen.append(cid)
    out = [cid for cid in names if cid in chosen]   # 保持出场顺序稳定（可复现）
    return out or names[:limit]


def silent_cast(roster: Iterable[str], order: Iterable[str]) -> list[str]:
    """本回合静默的角色（用于给写手/前端说明"其他人在场但没轮到"）。"""
    acting = {str(x) for x in (order or [])}
    return [str(x) for x in (roster or []) if str(x) not in acting]


