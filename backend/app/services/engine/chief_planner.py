"""主笔引擎（Chief Planner）：书级结构生成（S2）。

职责：把作者一句话方向扩成可执行骨架——世界观（含规则段）、章节清单（每章基调/张力曲线/字数）、
场景清单（舞台/在场角色/本场目标）、伏笔埋设计划、全局结局集合。
铁律：只结构、不写台词、不参与回合循环（对齐 docs/agent职责与prompt设计.md §2）。

子能力：
  - plan_skelly(direction, context)   产出骨架 JSON（预览态，不落库）
  - parse_world_rules(rules_text)     把「## 规则」自然语言段 LLM 转结构化 WorldRule[]
                                        （解析一次，运行时检测用正则关键词、0 token）
"""
from __future__ import annotations

import json
import logging

from app.services.engine.schema_retry import validate_and_retry
from app.services.llm.client import client as llm_client

logger = logging.getLogger(__name__)

# 骨架输出的结构化 schema（对齐设计稿 §2）
PLAN_SCHEMA = {
    "worldview": {
        "premise": "str",
        "rules_text": "str：## 规则 段（自然语言，一条一条，可被解析为规则）",
        "background": "str",
    },
    "chapters": [{
        "title": "str",
        "tone": "str: action|suspense|warmth|serene",
        "tension_curve": "str: raise|hold|release 或简写",
        "word_target": 2500,
        "summary": "str",
        "scenes": [{
            "title": "str",
            "stage_desc": "str",
            "cast": ["char_id str"],
            "initial_facts": ["str 环境事实"],
            "goal": "str 本场张力目标/结局条件",
        }],
    }],
    "foreshadow_plan": [{
        "text": "str", "type": "plot|character|theme|world",
        "buried_scene": "str 场景标题", "expected_close_scene": "str 场景标题",
    }],
    "ending_options": ["str"],
}

RULES_SCHEMA = [{
    "concept": "str",
    "constraint": "str",
    "rule_type": "str: negation|exclusive|conditional|positive",
    "keywords": ["str"],
    "constraint_keywords": ["str"],
}]

# 灵感卡生成（P0-1）：schema 与前端 InspirationCard 对齐（type/source/adopted 由服务层补）
CARDS_SCHEMA = [{
    "icon": "str（emoji，1 个）",
    "title": "str（≤12 字，情节点名称）",
    "desc": "str（1-2 句描述）",
    "type": "str: plot|character|world",
}]

_CARDS_PROMPT = """你是小说【主笔】。根据作者方向，生成 3~5 张有张力的灵感卡（情节/人物/世界观皆可）。
要求：
- 每张卡 1 个核心点子，尽量不要与既有骨架重复；
- title 简短（≤12 字），desc 一句钩子；
- type ∈ plot|character|world。
作者方向：{direction}
请按给定 JSON schema 输出数组。"""

# 确定性回退模板（无 LLM 时使用，保证链路完整可演示）
_FALLBACK_CARDS = [
    {"icon": "⚔️", "title": "废材觉醒", "desc": "主角在宗门大比前夜，被神秘古玉中的残魂点醒隐藏血脉。", "type": "plot"},
    {"icon": "🏛️", "title": "宗门背叛", "desc": "养育主角十年的师父为保圣子之位，暗中抽取他的玄脉。", "type": "plot"},
    {"icon": "🧭", "title": "秘境探险", "desc": "主角坠崖后进入上古剑冢，获得可吞噬血脉的残缺功法。", "type": "world"},
]


def _validate_plan(raw: object) -> str | None:
    """骨架校验器（0 token）：缺章节/缺标题/场景不是数组 → 返回错误描述，通过返回 None。"""
    if not isinstance(raw, dict):
        return "顶层必须是 JSON 对象"
    chapters = raw.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        return "缺少非空 chapters 数组"
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            return f"chapters[{i}] 必须是对象"
        if not str(ch.get("title") or "").strip():
            return f"chapters[{i}] 缺少 title"
        if not isinstance(ch.get("scenes"), list):
            return f"chapters[{i}] 缺少 scenes 数组"
    return None


def _validate_cards(raw: object) -> str | None:
    """灵感卡校验器：必须是数组且每项有 title。"""
    if not isinstance(raw, list):
        return "必须是数组"
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or not str(c.get("title") or "").strip():
            return f"cards[{i}] 缺少 title"
    return None


def _validate_rules(raw: object) -> str | None:
    """规则校验器：数组且每项有 concept / constraint。"""
    if not isinstance(raw, list):
        return "必须是数组"
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            return f"rules[{i}] 必须是对象"
        if not str(r.get("concept") or "").strip() or not str(r.get("constraint") or "").strip():
            return f"rules[{i}] 缺 concept 或 constraint"
    return None

_PLAN_PROMPT = """你是小说【主笔】（总编剧）。你的职责：把作者的一句话方向扩成可执行的书籍骨架。
只负责结构，绝不写现场台词；角色行为、对白、情绪都留给角色与导演涌现。
注意：
- 世界观里的规则一律以自然语言写进 rules_text 的「## 规则」章节，一条一条陈述
  （如「筑基境修士不能动用空间挪移」「核心传承只能由宗主一脉开启」，
  语气要可被机器判断：否定/排他/条件）。
- 每章 2~3 个场景；场景要给出舞台布置(stage_desc)与本场目标(goal)。
- ending_options 给出 2~4 个全局结局。
- foreshadow_plan 只列重要的伏笔（3~5 条），给埋设场景与期望回收场景。

作者方向：{direction}
已有信息：{context}
请严格按给定 JSON schema 输出。"""

_RULES_PROMPT = """你是世界观规则解析器。把作者/主笔写的规则文本转成结构化 WorldRule 数组。
规则类型：
- negation（否定）：如「筑基境修士不能动用空间挪移」→ keywords 含「筑基」「挪移」「空间跳跃」
- exclusive（排他）：如「核心传承只能由宗主一脉开启」→ keywords 含「核心传承」「传承」
- conditional（条件）：如「一旦结丹，寿元延长至五百年」
- positive（肯定）：其他能机械化判断的设定
每条给出 concept（主题概念）、constraint（约束原文）、keywords（检测用关键词）、
constraint_keywords（约束表述词，如「不能」「只能」）。
无法机械化检测的模糊描述可跳过。只输出数组，不要解释。

规则文本：
{rules_text}"""


async def plan_skelly(direction: str, context: str = "", inspirations: list[dict] | None = None) -> dict:
    """主笔产出书籍骨架（预览态）。LLM 不可用/产出非法 → 抛 ValueError（路由转 503）。

    阶段③：结构化输出经 `validate_and_retry` 校验——骨架缺章/缺标题/缺场景数组时
    带错误反馈重生成（≤ guard_retry_max 次），全部失败才抛错，**不落库**。
    上下文拼装：{context}（书 synopsis 等）+ {inspirations}（已采纳灵感卡 title/desc，
    P1-1 灵感→骨架落地，把"已在酝酿的设定"喂给主笔）。
    """
    if not llm_client.available:
        raise ValueError("主笔暂离场：模型未接入，无法规划")
    base = context or "（无）"
    if inspirations:
        lines = "\n".join(f"- {c.get('title', '')}：{c.get('desc', '')}"
                          for c in inspirations if c.get("title"))
        base += f"\n\n作者已在酝酿以下设定（规划时必须吸收/不矛盾）：\n{lines}"
    prompt = _PLAN_PROMPT.format(direction=direction or "（未提供，按经典玄幻开局生成）",
                                 context=base)
    raw = await validate_and_retry(llm_client, prompt, PLAN_SCHEMA, _validate_plan)
    return raw


CHARACTERS_SCHEMA = [{
    "name": "str 姓名（2-4 字）",
    "summary": "str 一句话人设（身份+处境）",
    "traits": ["str 人格特质 2-4 个"],
    "voice": "str 说话腔调（句长/语气词/用词层级，必须具体可辨）",
    "core_beliefs": ["str 核心信条 1-2 条"],
    "bottom_lines": ["str 性格底线 1-2 条（护栏依据）"],
    "goals": ["str 本阶段目标（可被导演调权）"],
}]

_CHARACTERS_PROMPT = """你是小说主笔。为这本书设计 3-5 个**主要角色卡**（供角色引擎演绎）。
要求：
1. 每个角色必须有**可区分的腔调**（voice 要具体：句长、口头禅、用词层级；禁止"性格开朗"这类套话）；
2. bottom_lines 是硬底线（护栏会据此拦 OOC 行为）；
3. goals 是本阶段的可调权目标（会随剧情调整）；
4. 角色之间要有张力（立场/利益/误解），不要一团和气。
已有信息：{context}
方向：{direction}
请严格按给定 JSON schema 输出数组。"""


def _validate_characters(raw) -> str | None:
    if not isinstance(raw, list) or not raw:
        return "角色卡必须是数组"
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            return f"characters[{i}] 缺少 name"
        if not str(c.get("summary") or "").strip():
            return f"characters[{i}] 缺少 summary（一句话人设）"
        if not str(c.get("voice") or "").strip():
            return f"characters[{i}] 缺少 voice（腔调必须可辨）"
    return None


async def generate_characters(direction: str, context: str = "") -> list[dict]:
    """主笔生成角色卡（补上"从零开局"缺的那一环）。

    之前角色卡**只能作者手写**（或 seed 灌）→ "骨架自动生成，角色为空，推演空转"。
    """
    prompt = _CHARACTERS_PROMPT.format(direction=direction or "（未提供，按经典玄幻开局）",
                                       context=context or "（无）")
    try:
        raw = await validate_and_retry(llm_client, prompt, CHARACTERS_SCHEMA, _validate_characters)
    except ValueError:
        raw = None
    if not raw:
        raw = [
            {"name": "林尘", "summary": "落魄剑修，背负灭门旧案",
             "traits": ["隐忍", "护短"], "voice": "话少，短句，常以「嗯」应人",
             "core_beliefs": ["剑不欺人"], "bottom_lines": ["不伤妇孺"], "goals": ["查明灭门真相"]},
            {"name": "苏晚", "summary": "药堂女掌柜，暗中查一桩旧毒案",
             "traits": ["冷静", "执拗"], "voice": "语气客气但句句试探，爱用反问",
             "core_beliefs": ["毒可杀人亦可救人"], "bottom_lines": ["不拿病人做试"],
             "goals": ["找出下毒者"]},
            {"name": "赵擎", "summary": "宗门执法堂主，与旧案有关",
             "traits": ["威严", "多疑"], "voice": "官腔，长句，常以前辈口吻训诫",
             "core_beliefs": ["规矩高于人情"], "bottom_lines": ["不亲手杀同门"],
             "goals": ["封住旧案"]},
        ]
    out: list[dict] = []
    for c in raw:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name[:12], "summary": str(c.get("summary") or "").strip(),
            "traits": [str(t) for t in (c.get("traits") or [])][:4],
            "voice": str(c.get("voice") or "").strip(),
            "core_beliefs": [str(t) for t in (c.get("core_beliefs") or [])][:2],
            "bottom_lines": [str(t) for t in (c.get("bottom_lines") or [])][:2],
            "goals": [str(t) for t in (c.get("goals") or [])][:2],
        })
    return out


async def generate_cards(direction: str) -> list[dict]:
    """主笔生成灵感卡（3~5 张）。LLM 不可用/产出非法 → 确定性回退模板卡。

    阶段③：经 `validate_and_retry` 校验（数组+每项有 title），重试仍失败 → 回退模板卡。
    返回规范化 dict 列表（icon/title/desc/type；source/adopted 由服务层落库时补）。
    """
    cards: list[dict] = []
    if llm_client.available and (direction or "").strip():
        try:
            raw = await validate_and_retry(
                llm_client, _CARDS_PROMPT.format(direction=direction),
                CARDS_SCHEMA, _validate_cards,
            )
            cards = [
                {
                    "icon": str(c.get("icon") or "✦")[:16],
                    "title": str(c.get("title") or "").strip()[:64],
                    "desc": str(c.get("desc") or "").strip(),
                    "type": str(c.get("type") or "plot") if str(c.get("type") or "") in ("plot", "character", "world") else "plot",
                }
                for c in raw if (c.get("title") or "").strip()
            ]
        except ValueError:
            logger.warning("[主笔] 灵感卡生成校验失败，回退模板卡")
    if not cards:
        cards = [dict(c) for c in _FALLBACK_CARDS]
    return cards[:5]


async def parse_world_rules(rules_text: str) -> list[dict]:
    """把「## 规则」自然语言段转结构化 WorldRule[]（model_cheap，解析一次）。

    阶段③：经 `validate_and_retry` 校验（数组+每项有 concept/constraint）；
    重试仍失败 → 返回 []（规则解析是辅助，不阻塞骨架落库），记日志告警。
    """
    if not llm_client.available or not (rules_text or "").strip():
        return []
    try:
        raw = await validate_and_retry(
            llm_client, _RULES_PROMPT.format(rules_text=rules_text),
            RULES_SCHEMA, _validate_rules,
        )
    except ValueError as e:
        logger.warning("[主笔] 世界规则解析校验失败，本次跳过：%s", e)
        return []
    rules: list[dict] = []
    for r in raw:
        if r.get("concept") and r.get("constraint"):
            rules.append({
                "concept": str(r["concept"]),
                "constraint": str(r["constraint"]),
                "rule_type": str(r.get("rule_type") or "positive"),
                "keywords": [str(k) for k in (r.get("keywords") or []) if k],
                "constraint_keywords": [str(k) for k in (r.get("constraint_keywords") or []) if k],
            })
    return rules


def dump_rules_text(worldview: dict) -> str:
    """从世界观 dict 提取 rules_text（兼容缺键）。"""
    return str((worldview or {}).get("rules_text") or "")