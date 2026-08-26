"""角色演绎（MVP §5.2 · §7 第1层护栏）。

分工（§4.4 视角隔离）：
  - 角色**感知注入的是自己的子集**：`perceivable_facts` 环境子集 + 自己 `beliefs` 人际子集
    + 自己的 `core_beliefs`/`dynamic_goals`(含权重与内因)。不给全量黑板 → 信息差由此涌现。
  - **第1层人设护栏(OOC)**：角色卡 `bottom_lines` 为判定依据，跑偏则回退。

决策本身：接 LLM 时用强模型；无 Key 时走 `decide_fn`（场景文件提供确定性反应脚本）。
"""
from __future__ import annotations

import logging
from typing import AsyncIterator, Callable, Optional

from app.config import settings
from app.schemas import models
from app.schemas.models import CharacterCard
from app.services.engine.world import WorldEngine
from app.services.llm.client import client as llm

logger = logging.getLogger(__name__)

# ---- 默认 ④ 层 思考/决策 JSON 模板（角色卡 think_schema/decide_schema 可覆盖） ----
_DEFAULT_THINK_SCHEMA = (
    '{"thought":"第一人称内心独白（简短）","emotion":"当前情绪",'
    '"reasoning":"对眼前局势的推理"}'
)
_DEFAULT_DECIDE_SCHEMA = (
    '{"kind":"dialogue|action|conflict","text":"一句话行动或台词",'
    '"new_fact":"若产生新世界事实则为一句事实陈述，否则为空字符串"}'
)


class CharacterEngine:
    def __init__(self, sim: "models.SimulationState"):
        self.sim = sim
        self.world = WorldEngine(sim)

    # ------------------------------------------------------------ 感知注入（视角隔离）
    def perceive_context(self, char_id: str) -> str:
        """角色感知注入（④层 Prompt 拼装源）。

        只读该角色子集（视角隔离铁律，不给全量黑板）：
          - 自我认知：性格 / 目标(带权重与因) / 底线 / 已知信念
          - 自我设定(④层)：static_world 承载的世界观/人物关系由 _compose_prompt 注入
          - 眼前动态现场：当前成立的环境事实（可见度裁剪）+ 近况事件
        """
        card = self.sim.characters[char_id]
        lines: list[str] = [f"你（{card.name}）：{card.summary}", "【性格】" + "、".join(card.traits)]

        lines.append("【底线】" + ("；".join(card.bottom_lines) if card.bottom_lines else "无"))

        goals = "；".join(
            f"{g.text}(权重{g.weight:.2f})" + (f"，因:{g.last_adjust_reason}" if g.last_adjust_reason else "")
            for g in card.dynamic_goals
        )
        lines.append("【目标】" + (goals or "—"))

        beliefs = self.world.beliefs_for(char_id)
        if beliefs:
            btxt = "；".join(f"「{b.text}」({b.channel.value})" for b in beliefs[-5:])
            lines.append("【你已知】" + btxt)
        else:
            lines.append("【你已知】(尚无一事)")

        # 动态现场：当前成立的环境事实（可见度裁剪，信息差来源）
        env = [f.text for f in self.world.perceivable_facts(char_id)]
        if env:
            lines.append("【眼前】" + "；".join(env))

        recent = [e for e in self.sim.events[-6:] if e.source.value == "character"]
        if recent:
            lines.append("【近况】" + "；".join(f"{e.actor}：{e.payload.get('text','')}" for e in recent))
        return "\n".join(lines)

    # ------------------------------------------------------------ 涌现决策
    async def propose_action(self, char_id: str, decide_fn: Callable[[str], Optional[dict]]) -> Optional[dict]:
        return await self.decide(char_id, decide_fn)

    # ------------------------------------------------------------ 思考层（Task 3）
    def _compose_prompt(self, card: CharacterCard, context: str, stage: str) -> str:
        """按角色卡 ④ 层模板拼装 LLM 提示（自我认知注入）。

        顺序：作者腔调(system_prompt) → 世界观设定(static_world) → 感知现场(context) → 要求。
        context 由 perceive_context 生成，仅含该角色感知子集（视角隔离）。
        """
        parts: list[str] = []
        if card.system_prompt:
            parts.append("【作者对你这角色的演绎要求】\n" + card.system_prompt)
        if card.static_world:
            parts.append("【世界观设定】(你作为剧中人所知的常识与人物关系)\n" + card.static_world)
        parts.append(context)
        parts.append("【要求】" + (
            "用一两句话写该角色此刻的内心独白（第一人称、简短有力，只写心里话，不要场景描写、不要推测旁白）。"
            if stage == "think" else "据此决定该角色本回合的行动。"
        ))
        return "\n".join(parts)

    async def think(self, char_id: str) -> Optional[dict]:
        """思考层：LLM 产内心独白/推理（结构化 JSON）。

        LLM 不可用(返回 None)时无回退，思考产物缺省；可用则把中间产物写入
        sim.scratch['thoughts'][char_id]，供 SSE / 第1层护栏参考。视角隔离由 perceive_context 保证。
        """
        card = self.sim.characters[char_id]
        schema = card.think_schema or _DEFAULT_THINK_SCHEMA
        context = self.perceive_context(char_id)
        result = await llm.call_strong(self._compose_prompt(card, context, "think"), json_schema=schema)
        if not isinstance(result, dict):
            logger.info("[think] %s → LLM 不可用/返回异常，无思考产物", card.name)
            return None
        logger.info(
            "[think] %s 内心独白=%s 情绪=%s 推理=%s",
            card.name,
            result.get("thought"),
            result.get("emotion"),
            result.get("reasoning"),
        )
        self.sim.scratch.setdefault("thoughts", {})[char_id] = result
        return result

    async def stream_think(self, char_id: str) -> AsyncIterator[str]:
        """思考层·流式变体（async generator）：逐 token 产出内心独白。

        用强模型流式输出，每 token 逐个 yield；LLM 不可用时直接结束（无输出）。
        产出全文写进 scratch['thoughts'][char_id]（SSE/护栏仍可取用）。
        decide 仍走结构化 JSON（护栏必须解析 action.kind/new_fact），思考不参与
        决策逻辑，因此可以自由流式。
        """
        card = self.sim.characters[char_id]
        context = self.perceive_context(char_id)
        prompt = self._compose_prompt(card, context, "think")
        if not llm.available:
            return
        chunks: list[str] = []
        async for tok in llm.stream_cheap_text(prompt, model=settings.model_strong):
            chunks.append(tok)
            yield tok
        if chunks:
            text = "".join(chunks)
            self.sim.scratch.setdefault("thoughts", {})[char_id] = {"thought": text}
            logger.info("[think-stream] %s 内心独白=%s", card.name, text)

    async def decide(self, char_id: str, decide_fn: Callable[[str], Optional[dict]]) -> Optional[dict]:
        """决策层：LLM 产 行动+台词+new_fact。

        LLM 不可用(返回 None)时回落现有 decide_fn（确定性回退脚本）。
        决策中间产物写入 sim.scratch['decisions'][char_id]，标注来源 llm/fallback，供 SSE 与护栏使用。
        """
        card = self.sim.characters[char_id]
        context = self.perceive_context(char_id)
        schema = card.decide_schema or _DEFAULT_DECIDE_SCHEMA

        action: Optional[dict] = None
        result = await llm.call_strong(self._compose_prompt(card, context, "decide"), json_schema=schema)
        if isinstance(result, dict):
            action = {
                "actor": char_id,
                "kind": str(result.get("kind") or "dialogue"),
                "text": str(result.get("text") or ""),
                "new_fact": str(result.get("new_fact") or ""),
            }
            logger.info(
                "[decide] %s → %s「%s」 new_fact=%s",
                card.name, action["kind"], action["text"], action["new_fact"] or "无",
            )
            self.sim.scratch.setdefault("decisions", {})[char_id] = {"llm": result}
        else:
            try:
                action = decide_fn(context)  # LLM 不可用 → 确定性回退脚本
            except Exception as e:  # noqa: BLE001
                logger.exception("[decide] %s 回退脚本异常: %s", card.name, e)
                action = None
            self.sim.scratch.setdefault("decisions", {})[char_id] = {"fallback": True}
            logger.info("[decide] %s → 回退脚本: %s", card.name, action)

        if not action:
            return None
        action.setdefault("actor", char_id)
        action.setdefault("kind", "dialogue")
        action.setdefault("text", "")
        return action

    # ------------------------------------------------------------ 第1层护栏（人设 OOC）
    def persona_guard(self, char_id: str, action: dict) -> tuple[bool, str]:
        card: CharacterCard = self.sim.characters[char_id]
        text = action.get("text", "")
        for bl in card.bottom_lines:
            for kw in self._violation_keywords(bl):
                if kw and kw in text:
                    return False, f"人设底线「{bl}」被触碰（含'{kw}'）"
        for cb in card.core_beliefs:
            neg = cb.replace("坚持", "").strip()
            if neg and f"不{neg}" in text:
                return False, f"与核心信条「{cb}」相反"
        return True, ""

    @staticmethod
    def _violation_keywords(bottom_line: str) -> list[str]:
        mapping = {
            "不施暴": ["动手", "殴打", "打他", "打她", "甩巴掌", "按倒在地"],
            "不背叛旧主": ["出卖", "背叛", "告密给", "出卖给"],
            "不承认不实指控": ["认罪", "是我杀的", "我承认杀了"],
            "不对家人说谎": ["骗", "撒谎"],
        }
        return mapping.get(bottom_line, [bottom_line])

    # ------------------------------------------------------------ 行动落库辅助
    def record_action(self, char_id: str, action: dict) -> "models.Event":
        payload = dict(action)
        text = str(payload.get("text", ""))
        ev = self.world.append_event(
            type_=models.EventType.action,
            source=models.EventSource.character,
            actor=char_id,
            payload={"kind": payload.get("kind", "dialogue"), "text": text, **payload},
        )
        return ev