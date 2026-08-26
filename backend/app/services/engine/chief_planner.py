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
    raw = await llm_client.call_cheap(prompt, PLAN_SCHEMA)
    if not isinstance(raw, dict) or not raw.get("chapters"):
        raise ValueError("主笔产出为空或结构非法")
    return raw


async def generate_cards(direction: str) -> list[dict]:
    """主笔生成灵感卡（3~5 张）。LLM 不可用/产出非法 → 确定性回退模板卡。

    返回规范化 dict 列表（icon/title/desc/type；source/adopted 由服务层落库时补）。
    """
    cards: list[dict] = []
    if llm_client.available and (direction or "").strip():
        raw = await llm_client.call_cheap(_CARDS_PROMPT.format(direction=direction),
                                          CARDS_SCHEMA)
        if isinstance(raw, list):
            cards = [
                {
                    "icon": str(c.get("icon") or "✦")[:16],
                    "title": str(c.get("title") or "").strip()[:64],
                    "desc": str(c.get("desc") or "").strip(),
                    "type": str(c.get("type") or "plot") if str(c.get("type") or "") in ("plot", "character", "world") else "plot",
                }
                for c in raw if (c.get("title") or "").strip()
            ]
    if not cards:
        cards = [dict(c) for c in _FALLBACK_CARDS]
    return cards[:5]


async def parse_world_rules(rules_text: str) -> list[dict]:
    """把「## 规则」自然语言段转结构化 WorldRule[]（model_cheap，解析一次）。"""
    if not llm_client.available or not (rules_text or "").strip():
        return []
    raw = await llm_client.call_cheap(_RULES_PROMPT.format(rules_text=rules_text),
                                      RULES_SCHEMA)
    if not isinstance(raw, list):
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