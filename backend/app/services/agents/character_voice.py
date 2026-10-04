"""角色在场感（E 批）：让**角色 agent 就当前正文说一句话**——只读意见卡，不改正文。

背景：写正文时角色 agent 并不在场（写手只拿到角色卡文本），这是"像不像人"的最后一公里。
**不破边界**（沿用已有硬约束）：
  · 角色 agent **不允许发起任务**（AgentSpec.can_initiate_tasks=False，A+ 已强制）；
  · 本入口是 `read`：产出意见卡，不落正文、不改账本、不写伏笔；
  · 感知来源 = 角色卡（summary/腔调/底线）+ **该角色在本文里的 0-token 腔调统计** + 与其相关的正文片段。
与回合内的 character agent 区别：那边读的是**运行时 sim**（视角隔离），这边读的是**当前正文**，
所以定位为"就这一段说说"，不是回合推演。
"""
from __future__ import annotations

from app.services.engine.schema_retry import validate_and_retry
from app.services.engine.style_checks import per_character_metrics
from app.services.llm.client import client as llm_client  # 模块级引用：便于测试统一替换

MAX_CHARS = 3          # 一次最多请几个角色（控成本）
SNIPPET_CHARS = 260

VOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "emotion": {"type": "string"},
        "monologue": {"type": "string"},
        "critique": {"type": "string"},
        "line": {"type": "string"},
    },
    "required": ["monologue"],
}


def _validate(raw: object) -> str | None:
    """0-token 校验（契约：None=通过；字符串=错误描述）。"""
    if not isinstance(raw, dict):
        return "输出必须是 JSON 对象"
    mono = raw.get("monologue")
    if not isinstance(mono, str) or len(mono.strip()) < 4:
        return "monologue 必须是至少 4 个字的心里话"
    return None


def snippet_for(text: str, name: str) -> str:
    """挑出与该角色相关的正文片段（提到他/她的段落；没有就取开头）。"""
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    hits = [p for p in paras if name and name in p]
    picked = hits[:2] if hits else paras[:2]
    out = "\n".join(picked)[:SNIPPET_CHARS]
    return out or "（空白）"


def build_prompt(card: dict, spec: dict, metrics: dict, snippet: str) -> str:
    traits = "、".join(spec.get("traits") or []) or "（未填）"
    bottom = "；".join(spec.get("bottom_lines") or []) or "（未填）"
    avg = metrics.get("avg_len")
    particles = "、".join(metrics.get("particles") or []) or "（未统计）"
    lines = [
        f"你正在扮演小说角色【{card.get('name', '')}】。你不是 AI，你就是这个人。",
        f"【你的设定】{spec.get('summary') or '（未填）'}",
        f"【性格】{traits}",
        f"【说话腔调】{spec.get('voice') or '（未填）'}",
        f"【底线】{bottom}",
        f"【你在这段文字里的腔调统计｜0-token】平均句长约 {avg if avg is not None else '—'} 字；语气词：{particles}",
        f"【与你有关的正文片段】\n{snippet}",
        "【要求】用第一人称说 2-3 句心里话，回答：这段话像不像你说的？哪一句不像？",
        "输出 JSON：{\"emotion\":\"此刻情绪\",\"monologue\":\"心里话(2-3 句)\","
        "\"critique\":\"哪一句不像你说的（逐字引用原句）\",\"line\":\"换成你会怎么说（一句，不要改剧情事实）\"}。",
        "红线：只能改「怎么说」，不得改「发生了什么」；不要复述剧情，不要评价写作技巧。",
    ]
    return "\n\n".join(lines)


async def build_voices(service, scene_id: str, text: str = "", limit: int = MAX_CHARS) -> dict:
    scene, _ = await service._scene_and_book(scene_id)
    body = (text or "").strip() or str(scene.get("final_prose") or "")
    if not body:
        return {"scene_id": scene_id, "opinions": [], "note": "正文为空：先让写手出初稿，再请角色说一句"}

    cards = await service.repo.list_characters_for_scene(scene_id)
    if not cards:
        return {"scene_id": scene_id, "opinions": [], "note": "本场未配置上场角色（可在人物页生成角色卡）"}

    metrics_by_name = {str(m.get("name")): m for m in per_character_metrics(body, cards)}
    opinions: list[dict] = []
    for c in cards[:limit]:
        name = str(c.get("name") or "")
        spec = c.get("spec") or {}
        metrics = metrics_by_name.get(name) or {}
        base = {
            "char_id": c.get("id", ""), "name": name,
            "voice": spec.get("voice", ""), "avg_len": metrics.get("avg_len"),
            "particles": metrics.get("particles") or [],
        }
        if not getattr(llm_client, "available", False):
            opinions.append({**base, "emotion": "", "monologue": "", "critique": "", "line": "",
                             "note": "模型未接入：只能给出 0-token 腔调统计"})
            continue
        try:
            raw = await validate_and_retry(
                llm_client, build_prompt(c, spec, metrics, snippet_for(body, name)), VOICE_SCHEMA, _validate
            )
        except ValueError as e:
            opinions.append({**base, "emotion": "", "monologue": "", "critique": "", "line": "",
                             "note": f"没生成有效意见：{e}"})
            continue
        opinions.append({
            **base,
            "emotion": str(raw.get("emotion") or ""),
            "monologue": str(raw.get("monologue") or ""),
            "critique": str(raw.get("critique") or ""),
            "line": str(raw.get("line") or ""),
        })

    return {
        "scene_id": scene_id,
        "opinions": opinions,
        "note": "角色意见只读：不改正文、不改账本、不写伏笔（角色 agent 也不允许发起任务）",
    }
