"""正文协作四角色能力（阶段④ · engine/prose.py）。

多角色分工的正文协作：✍ 写手(draft) / 🩺 体检员(review) / 🎨 润色师(polish，反AI味、只改写法) /
🔍 质检员(verify，伏笔/信念/因果对照)。每个结构化产出复用 `validate_and_retry` 纠错；
无 LLM/重试仍失败 → 确定性回退（不返回脏数据、不抛 500）。
只做能力（prompt→LLM→校验→回退），不读写库（Service 层负责场景/账本/notes）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from app.services.engine.schema_retry import validate_and_retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- JSON schema
REVIEW_SCHEMA = {
    "issues": [{"severity": "str: high|mid|low", "text": "str 问题", "suggestion": "str 建议"}],
    "overall": "str ≤80 字总评",
}

POLISH_SCHEMA = {"after": "str 润色后全文", "summary": "str ≤120 字改动摘要"}

VERIFY_SCHEMA = {
    "foreshadow_updates": [{"text": "str 伏笔命中/待推进", "status": "str: in_progress|closed", "reason": "str"}],
    "belief_deltas": [{"char": "str 角色名", "text": "str 认知变化", "channel": "str: perceived|told|inferred"}],
    "causal": ["str 因果补记"],
    "risks": ["str 一致性风险提示"],
    "state_deltas": [{
        "kind": "str: character|item|time|term|numeric",
        "name": "str 状态对象名（林尘/屠龙剑/第3天/灵石/境界）",
        "key": "str 语义键（char:林尘:境界 / item:林尘:屠龙剑 / time:全书:当前日 / term:通用:灵石 / numeric:林尘:战力）",
        "value": "str 当前值",
        "previous_value": "str 前值（可为空）",
    }],
}

# ---------------------------------------------------------------- prompts
def _scene_block(scene: dict) -> str:
    return (
        f"标题：{scene.get('title') or ''}\n"
        f"舞台：{scene.get('stage_desc') or ''}\n"
        f"本场目标：{scene.get('goal') or ''}\n"
        f"内容描述：{scene.get('content_desc') or ''}"
    )


def _characters_block(characters: list[dict]) -> str:
    """将角色卡列表格式化为 prompt 文本块。

    字段名必须对齐 `schemas/models.py::CharacterCard`（summary/traits/voice/
    core_beliefs/bottom_lines/system_prompt）——早期误用 personality/tone/bottom_line
    旧名，导致每个角色都渲染成"（无详细设定）"，角色卡等于没注入（B16）。
    """
    if not characters:
        return "（本场无指定角色）"
    lines = []
    for c in characters:
        name = c.get("name", "未知")
        spec = c.get("spec_json", "{}")
        try:
            import json as _j
            card = _j.loads(spec) if isinstance(spec, str) else spec
        except Exception:
            card = {}
        if not isinstance(card, dict):
            card = {}
        parts: list[str] = []
        if card.get("summary"):
            parts.append(str(card["summary"]))
        traits = [str(t) for t in (card.get("traits") or []) if str(t).strip()]
        if traits:
            parts.append("特质：" + "、".join(traits))
        if card.get("voice"):
            parts.append("腔调：" + str(card["voice"]))
        core = [str(x) for x in (card.get("core_beliefs") or []) if str(x).strip()]
        if core:
            parts.append("信条：" + "、".join(core))
        bottom = [str(x) for x in (card.get("bottom_lines") or []) if str(x).strip()]
        if bottom:
            parts.append("底线：" + "；".join(bottom))
        if card.get("system_prompt"):
            parts.append("作者演绎要求：" + str(card["system_prompt"]))
        desc = "；".join(parts) if parts else "（未填角色卡：请按名字自设一致腔调，勿套用通用音色）"
        lines.append(f"- {name}：{desc}")
    return "\n".join(lines)


def _world_states_block(world_states: list[dict] | None) -> str:
    """将世界状态账本列表格式化为 prompt 文本块（只列非空、过滤作者手删的项）。"""
    if not world_states:
        return "（暂无世界状态记录）"
    lines = []
    for s in world_states:
        name = s.get("name") or ""
        value = s.get("value") or ""
        if not name or not value:
            continue
        kind = s.get("kind") or ""
        edited = s.get("edited")
        marker = "（作者手改）" if edited else ""
        lines.append(f"- [{kind}] {name}：{value}{marker}")
    return "\n".join(lines) if lines else "（暂无世界状态记录）"


_DRAFT_PROMPT = """你是小说正文【写手】。根据场景设定、出场角色、世界状态与前文，写一段有画面感的小说正文（旁白+行动+对话推进）。
要求：中文，150-220 字；动作留白、有环境描写；符合角色性格和腔调；只写正文不要任何解释/标题。
场景设定：
{scene}

出场角色（按角色卡演绎，注意性格/腔调/底线）：
{characters}

世界状态现状（角色战力/道具/时间/术语/关键数字，务必与账本一致，无新增不要编造）：
{world_states}

前文摘要（承接上文，保持连贯）：
{prev_prose}

书级记忆（务必遵守）：
{memory}

{existing_block}"""

_REVIEW_PROMPT = """你是小说【审核体检员】。审读下面正文，给结构化体检报告（结构/逻辑/节奏/人设一致性/用词问题）。
场景设定（作为体检基准）：
{scene}
正文：
{text}
输出 issues 数组（severity=high|mid|low），没问题的项不要编造。"""

_POLISH_PROMPT = """你是小说【润色师】。对下面正文做「去 AI 味」润色：
- 只改写法（句式/节奏/措辞），**绝不改变剧情事实、人物言行、伏笔信息**；
- 打散工整排比、去掉"然而/顿时/仿佛"等套话、让叙述更口语有棱角；
- 输出 after（润色后全文）+ summary（≤120 字改动摘要）。
原正文：
{text}"""

_VERIFY_PROMPT = """你是小说【质检员】。对照这本书的伏笔账本、角色信念账本与世界状态账本，审读正文，给出结构化质检意见：
1. foreshadow_updates：正文是否命中/推进了未回收伏笔（只对账本中已有的伏笔，不得凭空新增）；
2. belief_deltas：正文是否改变了某角色认知（对账本中已有信念角色）；
3. causal：正文中的新事实/事件的因果补记；
4. risks：与设定/账本冲突的提示；
5. state_deltas：正文揭示的状态变化（当前值/持续态，不是一次性动作）。value 严格填**有据的持续性状态值**——
   - character：境界/位置/身份/受伤度等（如 "元婴初期"）
   - item：携带/缺失/耗尽/损坏等（如 "携带"，不是 "震颤/挥剑"）
   - time：当前叙事日/时间（如 "第3天"）
   - term：锁定术语的标准叫法（如 "灵石"）
   - numeric：年龄/战力/金钱/距离（如 "战力=1200"）
   previous_value 用账本当前值；正文首次出现的关键状态则留空。只报变化或新关键状态。
伏笔账本现状：
{foreshadows}
信念账本现状：
{beliefs}
世界状态账本现状（只报变化或新关键状态，其余不改）：
{world_states}
正文：
{text}"""

# ---------------------------------------------------------------- validators
REVIEW_VALIDATOR: Callable[[Any], str | None] = lambda raw: (
    None if isinstance(raw, dict) and isinstance(raw.get("issues"), list)
    else "体检报告必须是含 issues 数组的对象"
)
POLISH_VALIDATOR: Callable[[Any], str | None] = lambda raw: (
    None if isinstance(raw, dict) and str(raw.get("after") or "").strip()
    else "润色输出必须是含非空 after 的对象"
)
VERIFY_VALIDATOR: Callable[[Any], str | None] = lambda raw: (
    None if isinstance(raw, dict) and all(k in raw for k in ("foreshadow_updates", "belief_deltas", "causal", "risks", "state_deltas"))
    else "质检意见缺少必需的五个字段"
)
DRAFT_VALIDATOR: Callable[[Any], str | None] = lambda raw: (
    None if isinstance(raw, str) and len(raw.strip()) >= 20
    else "正文过短（<20 字）"
)

# ---------------------------------------------------------------- role runtimes
async def _run_role(
    llm_client: Any,
    prompt: str,
    schema: Any,
    validator: Callable[[Any], str | None],
    fallback: Any,
    retries: int | None = None,
) -> Any:
    """共享样板：LLM 结构化调用 + 纠错重试；不可用/熔断 → 确定性回退。"""
    if not llm_client.available:
        return fallback
    try:
        return await validate_and_retry(llm_client, prompt, schema, validator, retries=retries)
    except ValueError as e:
        logger.warning("[prose] 结构化产出校验失败，回退：%s", e)
        return fallback


async def draft_prose(llm_client: Any, scene: dict, memory: str,
                      existing: str = "", characters: list[dict] | None = None,
                      prev_prose: str = "",
                      world_states: list[dict] | None = None) -> str:
    """写手：正文初稿（自由文本，非结构化；无 LLM → 空串由调用方提示）。"""
    if not llm_client.available:
        return ""
    existing_block = f"已有正文（可续写或改写）：\n{existing}" if existing.strip() else "（新写开场）"
    prompt = _DRAFT_PROMPT.format(
        scene=_scene_block(scene),
        characters=_characters_block(characters or []),
        world_states=_world_states_block(world_states),
        prev_prose=prev_prose or "（无前文，本场为开场）",
        memory=memory or "（无）",
        existing_block=existing_block,
    )
    raw = await validate_and_retry(llm_client, prompt, None, DRAFT_VALIDATOR)
    return str(raw).strip()


async def review_prose(llm_client: Any, scene: dict, text: str) -> dict:
    """体检员：结构化体检报告；回退 → 空问题报告。"""
    return await _run_role(
        llm_client,
        _REVIEW_PROMPT.format(scene=_scene_block(scene), text=text or "（空）"),
        REVIEW_SCHEMA, REVIEW_VALIDATOR,
        {"issues": [], "overall": "（模型未接入，本次体检跳过）"},
    )


async def polish_prose(llm_client: Any, text: str) -> dict:
    """润色师：只改写法；回退 → 原文 + 未润色摘要。"""
    return await _run_role(
        llm_client,
        _POLISH_PROMPT.format(text=text or "（空）"),
        POLISH_SCHEMA, POLISH_VALIDATOR,
        {"after": text, "summary": "（模型未接入，未润色）"},
    )


async def verify_prose(llm_client: Any, text: str, foreshadows: list[dict],
                       beliefs: list[dict], world_states: list[dict] | None = None) -> dict:
    """质检员：伏笔/信念/因果/世界状态对照；回退 → 空意见。"""
    f_txt = "\n".join(f"- {f.get('text')}（{f.get('status')}）" for f in foreshadows) or "（无未回收伏笔）"
    b_txt = "\n".join(f"- {b.get('char_id')}：{b.get('text')}" for b in beliefs if b.get("text")) or "（无信念记录）"
    ws_txt = "\n".join(
        f"- [{s.get('kind')}] {s.get('name')}：{s.get('value')}"
        for s in (world_states or []) if s.get("name")
    ) or "（无世界状态记录）"
    return await _run_role(
        llm_client,
        _VERIFY_PROMPT.format(foreshadows=f_txt, beliefs=b_txt, world_states=ws_txt, text=text or "（空）"),
        VERIFY_SCHEMA, VERIFY_VALIDATOR,
        {"foreshadow_updates": [], "belief_deltas": [], "causal": [], "risks": [], "state_deltas": []},
    )