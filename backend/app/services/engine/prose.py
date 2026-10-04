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
    "voice_findings": [{
        "char": "str 角色名",
        "evidence": "str 正文原句（逐字摘录，不得改写）",
        "issue": "str 哪里不像（语气词/句长/称呼/用词层级）",
        "suggestion": "str 改成什么更贴卡",
    }],
    "overall": "str ≤80 字总评",
}

POLISH_SCHEMA = {"after": "str 润色后全文", "summary": "str ≤120 字改动摘要"}

VERIFY_SCHEMA = {
    "foreshadow_updates": [{"text": "str 伏笔命中/待推进", "status": "str: in_progress|closed", "reason": "str"}],
    "belief_deltas": [{"char": "str 角色名", "text": "str 认知变化", "channel": "str: perceived|told|inferred"}],
    "causal": ["str 因果补记"],
    "risks": ["str 一致性风险提示"],
    "voice_risks": [{
        "char": "str 角色名",
        "evidence": "str 正文原句（逐字摘录，不得改写）",
        "risk": "str 口吻风险（与卡内腔调/底线/称呼/用词层级不一致之处）",
        "suggestion": "str 改成什么更贴卡",
    }],
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
要求：中文，150-220 字；动作留白、有环境描写；只写正文不要任何解释/标题。
口吻纪律（必须遵守）：
- 每个角色的台词与动作必须对得上其「腔调」：句长、语气词、用词层级要能对号入座；
- 多角色同场时，禁止所有人共用同一种句式/口头禅，各角色说话方式必须可区分；
- 角色互称必须与卡内关系一致，同一关系不得在文内换称呼；
- 角色没开口就不要替他/她说话；不要把所有角色都写成同一种"文雅"腔。
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

书级约束（方向 / 世界观 / 硬规则 / 写作约束 —— 全员必须遵守，由主笔维护）：
{constraints}

涌现素材（作者已采纳的剧本高光，当素材参考、不必照抄）：
{emergence}

角色动机依据（来自推演回合的角色推理，用于对齐"为什么这么做"）：
{motives}

{existing_block}"""

_REVIEW_PROMPT = """你是小说【审核体检员】。审读下面正文，给结构化体检报告（结构/逻辑/节奏/人设一致性/用词问题）。
场景设定（作为体检基准）：
{scene}

出场角色卡（口吻体检基准，逐字对照其"腔调"）：
{characters}

0-token 口吻先验（确定性统计，可直接引用，不必自行估算；与角色卡矛盾时优先怀疑正文）：
{voice_prior}

正文：
{text}

输出要求：
1. issues 数组（severity=high|mid|low），没问题的项不要编造；
2. voice_findings 数组：逐条指出"某角色的台词/动作不像其卡内腔调"的问题。每条必须带
   char（角色名）与 evidence（正文原句，**逐字摘录**）；找不到原句的猜测一律不要输出。
   若各角色腔调都立得住，voice_findings 返回空数组。"""

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
5. voice_risks：逐条指出"某角色的台词/动作**不像其卡内腔调**"的口吻风险。每条必须带 char（角色名）与
   evidence（正文原句，**逐字摘录、不得改写**）；找不到原句的猜测一律不要输出；各角色腔调都立得住则给空数组。
6. state_deltas：正文揭示的状态变化（当前值/持续态，不是一次性动作）。value 严格填**有据的持续性状态值**——
   - character：境界/位置/身份/受伤度等（如 "元婴初期"）
   - item：携带/缺失/耗尽/损坏等（如 "携带"，不是 "震颤/挥剑"）
   - time：当前叙事日/时间（如 "第3天"）
   - term：锁定术语的标准叫法（如 "灵石"）
   - numeric：年龄/战力/金钱/距离（如 "战力=1200"）
   previous_value 用账本当前值；正文首次出现的关键状态则留空。只报变化或新关键状态。
出场角色卡（口吻质检基准，逐字对照其"腔调"）：
{characters}
伏笔账本现状：
{foreshadows}
信念账本现状：
{beliefs}
世界状态账本现状（只报变化或新关键状态，其余不改）：
{world_states}
正文：
{text}"""

# ---------------------------------------------------------------- validators
def _validate_review(raw: Any) -> str | None:
    """体检报告：issues 必填；voice_findings 若给出必须是数组（可为空）。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("issues"), list):
        return "体检报告必须是含 issues 数组的对象"
    vf = raw.get("voice_findings")
    if vf is not None and not isinstance(vf, list):
        return "voice_findings 必须是数组（无问题也要给空数组）"
    return None


REVIEW_VALIDATOR: Callable[[Any], str | None] = _validate_review
POLISH_VALIDATOR: Callable[[Any], str | None] = lambda raw: (
    None if isinstance(raw, dict) and str(raw.get("after") or "").strip()
    else "润色输出必须是含非空 after 的对象"
)
def _validate_verify(raw: Any) -> str | None:
    """质检意见：五个必需字段 + voice_risks 若给出必须是数组。

    校验失败时**逐一点名缺失字段**（而非笼统"缺字段"），让 validate_and_retry
    的反馈重试能精准补齐（S2 step4）。
    """
    if not isinstance(raw, dict):
        return "质检意见必须是对象"
    need = ("foreshadow_updates", "belief_deltas", "causal", "risks", "state_deltas")
    missing = [k for k in need if k not in raw]
    if missing:
        return "质检意见缺少必需字段：" + "、".join(missing)
    vr = raw.get("voice_risks")
    if vr is not None and not isinstance(vr, list):
        return "voice_risks 必须是数组（无问题也要给空数组）"
    return None


VERIFY_VALIDATOR: Callable[[Any], str | None] = _validate_verify
DRAFT_VALIDATOR: Callable[[Any], str | None] = lambda raw: (
    None if isinstance(raw, str) and len(raw.strip()) >= 20
    else "正文过短（<20 字）"
)

_QUOTE_CHARS = str.maketrans({c: "" for c in "“”\"‘’ \t\n"})


def _sanitize_grounded(report: Any, text: str, cast_names: set[str] | None,
                      field: str, note_key: str) -> Any:
    """逐字证据闸门（0 token）——体检口吻检点 / 质检口吻风险共用。

    保留条件：①char 非空且在出场名单内；②evidence 非空且能在正文里逐字找到。
    任一不满足 → 丢弃该条（宁可漏报，不可编造"某句不像他说的"）。
    """
    if not isinstance(report, dict):
        return report
    raw = report.get(field)
    if not isinstance(raw, list):
        report[field] = []
        return report
    haystack = (text or "").translate(_QUOTE_CHARS)
    kept: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        char = str(item.get("char") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if not char or not evidence:
            continue
        if cast_names and char not in cast_names:
            continue
        if evidence.translate(_QUOTE_CHARS) not in haystack:
            continue
        kept.append({
            "char": char,
            "evidence": evidence,
            note_key: str(item.get(note_key) or "").strip(),
            "suggestion": str(item.get("suggestion") or "").strip(),
        })
    report[field] = kept
    return report


def _sanitize_voice_findings(report: Any, text: str, cast_names: set[str] | None = None) -> Any:
    """体检员：voice_findings 逐字证据闸门（保留 issue 字段）。"""
    return _sanitize_grounded(report, text, cast_names, "voice_findings", "issue")


def _sanitize_voice_risks(report: Any, text: str, cast_names: set[str] | None = None) -> Any:
    """质检员：voice_risks 逐字证据闸门（保留 risk 字段）。"""
    return _sanitize_grounded(report, text, cast_names, "voice_risks", "risk")


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


def _emergence_block(materials: list[dict] | None) -> str:
    """S4 桥接：作者已采纳的涌现高光素材（title：desc）。"""
    rows = []
    for m in materials or []:
        title = str((m or {}).get("title") or "").strip()
        desc = str((m or {}).get("desc") or "").strip()
        if title or desc:
            rows.append("- %s：%s" % (title, desc))
    return "\n".join(rows) if rows else "（无）"


def _motives_block(motives: list[dict] | None) -> str:
    """S4 桥接：角色动机依据（thoughts.reasoning）—— 与"内心独白"分开，只给动机。"""
    rows = []
    for m in motives or []:
        char = str((m or {}).get("char") or "").strip()
        why = str((m or {}).get("reasoning") or "").strip()
        if why:
            rows.append("- %s：%s" % (char, why))
    return "\n".join(rows) if rows else "（无）"


async def draft_prose(llm_client: Any, scene: dict, memory: str,
                      existing: str = "", characters: list[dict] | None = None,
                      prev_prose: str = "",
                      world_states: list[dict] | None = None,
                      emergence: list[dict] | None = None,
                      motives: list[dict] | None = None,
                      constraints: str = "") -> str:
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
        constraints=constraints or "（无）",
        emergence=_emergence_block(emergence),
        motives=_motives_block(motives),
        existing_block=existing_block,
    )
    raw = await validate_and_retry(llm_client, prompt, None, DRAFT_VALIDATOR)
    return str(raw).strip()


async def review_prose(llm_client: Any, scene: dict, text: str,
                       characters: list[dict] | None = None,
                       voice_prior: dict | None = None) -> dict:
    """体检员：结构化体检报告（含口吻检点，对照角色卡）；回退 → 空问题报告。

    voice_prior：S2 step5 的 0-token 先验（由调用方用 style_checks.voice_prior() 算好），
    作为"确定性事实"注入 prompt，让体检有客观锚点、少靠模型自行估算。
    """
    from app.services.engine.style_checks import prior_block

    cast_names = {str(c.get("name") or "").strip() for c in (characters or []) if c.get("name")}
    report = await _run_role(
        llm_client,
        _REVIEW_PROMPT.format(
            scene=_scene_block(scene),
            characters=_characters_block(characters or []),
            voice_prior=prior_block(voice_prior or {}),
            text=text or "（空）",
        ),
        REVIEW_SCHEMA, REVIEW_VALIDATOR,
        {"issues": [], "voice_findings": [], "overall": "（模型未接入，本次体检跳过）"},
    )
    return _sanitize_voice_findings(report, text, cast_names)


async def polish_prose(llm_client: Any, text: str) -> dict:
    """润色师：只改写法；回退 → 原文 + 未润色摘要。"""
    return await _run_role(
        llm_client,
        _POLISH_PROMPT.format(text=text or "（空）"),
        POLISH_SCHEMA, POLISH_VALIDATOR,
        {"after": text, "summary": "（模型未接入，未润色）"},
    )


async def verify_prose(llm_client: Any, text: str, foreshadows: list[dict],
                       beliefs: list[dict], world_states: list[dict] | None = None,
                       characters: list[dict] | None = None) -> dict:
    """质检员：伏笔/信念/因果/世界状态/口吻风险对照；回退 → 空意见。"""
    cast_names = {str(c.get("name") or "").strip() for c in (characters or []) if c.get("name")}
    f_txt = "\n".join(f"- {f.get('text')}（{f.get('status')}）" for f in foreshadows) or "（无未回收伏笔）"
    b_txt = "\n".join(f"- {b.get('char_id')}：{b.get('text')}" for b in beliefs if b.get("text")) or "（无信念记录）"
    ws_txt = "\n".join(
        f"- [{s.get('kind')}] {s.get('name')}：{s.get('value')}"
        for s in (world_states or []) if s.get("name")
    ) or "（无世界状态记录）"
    report = await _run_role(
        llm_client,
        _VERIFY_PROMPT.format(
            characters=_characters_block(characters or []),
            foreshadows=f_txt, beliefs=b_txt, world_states=ws_txt, text=text or "（空）",
        ),
        VERIFY_SCHEMA, VERIFY_VALIDATOR,
        {"foreshadow_updates": [], "belief_deltas": [], "causal": [], "risks": [],
         "voice_risks": [], "state_deltas": []},
    )
    report = _sanitize_voice_risks(report, text, cast_names)
    # 口吻风险同时在 risks 里补一行人类可读提示（并增：前端不改也能在质检摘要看到）
    for v in report.get("voice_risks") or []:
        line = f"口吻：{v.get('char')}「{str(v.get('evidence') or '')[:24]}」{str(v.get('risk') or '')}"
        if isinstance(report.get("risks"), list) and line not in report["risks"]:
            report["risks"].append(line)
    return report