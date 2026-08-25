"""Schema 层 · Pydantic 领域模型：对齐 MVP §4 数据地基 + §6 导演状态 + §7 护栏统计。

全部用 Pydantic 硬约束，防止 LLM 乱写（MVP §5.1「结构化约束 Pydantic」）。
字段语义按终态设计，MVP 先用最小集，避免后期迁移。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- 枚举
class EventType(str, Enum):
    private_percept = "private_percept"   # 个体私感知
    action = "action"                      # 角色行动/发言
    environment = "environment"            # 环境事件
    director_hint = "director_hint"        # 导演软引导
    text = "text"                          # 成文渲染产物


class EventSource(str, Enum):
    character = "character"
    director = "director"
    reader = "reader"
    system = "system"


class BeliefChannel(str, Enum):
    perceived = "perceived"    # 亲见
    told = "told"              # 被告知
    inferred = "inferred"      # 推测


class GuardLayer(str, Enum):
    persona = "persona"        # 第1层 人设护栏(OOC)
    world = "world"            # 第3层 世界事实护栏


# --------------------------------------------------------------------------- 4.1 角色卡（静态）
class Goal(BaseModel):
    id: str
    text: str                                  # 动态目标文本
    weight: float = Field(default=0.5, ge=0.0, le=1.0)   # 可被导演调整
    last_adjust_reason: str = ""               # 权重调整必填的剧情内因(§6.1因果律)


class CharacterCard(BaseModel):
    id: str
    name: str
    summary: str                               # 人设 summary
    traits: list[str] = Field(default_factory=list)       # 人格特质
    voice: str = ""                            # 说话腔调
    core_beliefs: list[str] = Field(default_factory=list) # 核心信条
    dynamic_goals: list[Goal] = Field(default_factory=list)  # 可被导演调权重的动态目标
    bottom_lines: list[str] = Field(default_factory=list)    # 性格底线(第1层护栏依据)
    # ---- ④层 角色卡可编辑演绎 prompt 模板（docs/prompt核心设定.md，作者可在前端人物页改）----
    # 均默认空 → 运行时用内置默认模板；作者填写后覆盖默认，参与感知→思考→决策拼装。
    system_prompt: str = ""                    # 角色 system 指令（腔调/决策偏好，可含占位 {self}）
    think_schema: str = ""                     # 思考环节 JSON 输出约束(如要求"reazon/emotion"字段)
    decide_schema: str = ""                    # 决策环节 JSON 输出约束(如"action/text/new_fact")
    static_world: str = ""                     # 该角色可读的静态设定(暂承载世界/人物关系,未来抽顶层)


# --------------------------------------------------------------------------- 4.2 事实 / 世界状态 / 黑板
class Fact(BaseModel):
    id: str
    text: str                                   # 规范陈述，如"保险柜门半开"
    kind: str = "env"                           # env 环境事实
    visible_to: list[str] = Field(default_factory=list)   # 谁能感知(环境事实)
    created_event_id: str = ""
    active: bool = True                         # 事实是否仍成立(第3层护栏管)


class WorldState(BaseModel):
    scene_id: str
    title: str
    turn: int = 0
    settings: dict[str, Any] = Field(default_factory=dict)  # 初始世界观(静态)
    env_conds: list[str] = Field(default_factory=list)      # 环境条件(天气/时局)
    facts: list[Fact] = Field(default_factory=list)          # 已确认世界事实(第3层校验)
    timeline: list[str] = Field(default_factory=list)        # 事件 id 引用流


# --------------------------------------------------------------------------- 4.3 事件日志（append-only）
class Event(BaseModel):
    id: str
    turn: int
    type: EventType
    source: EventSource
    actor: str = ""                            # 角色 id 或 导演/系统
    payload: dict[str, Any] = Field(default_factory=dict)
    world_snapshot: dict[str, Any] = Field(default_factory=dict)  # 回放基线
    guard_flags: list[str] = Field(default_factory=list)  # 命中护栏层/熔断标记
    ts: int = 0


# --------------------------------------------------------------------------- 4.4 角色信念账本
class Belief(BaseModel):
    char_id: str
    fact_id: str
    source_event_id: str                       # 溯源:怎么知道的(必填,§4.4)
    channel: BeliefChannel                    # 亲见/被告知/推测
    text: str                                  # 该角色认知里的表述
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    ts: int = 0


# --------------------------------------------------------------------------- 导演状态 (§6)
class InfoExposure(BaseModel):
    fact_id: str
    target_char_id: str
    channel: BeliefChannel = BeliefChannel.perceived


class GoalAdjust(BaseModel):
    char_id: str
    goal_id: str
    delta: float
    reason: str = ""                           # 必填剧情内因(§6.1因果律硬约束)


class DirectorHint(BaseModel):
    stage_prompt: str = ""                     # 舞台提示:让谁先开口
    info_exposures: list[InfoExposure] = Field(default_factory=list)   # 信息曝光
    goal_adjusts: list[GoalAdjust] = Field(default_factory=list)       # 权重调整(带内因)


class RaiseRequest(BaseModel):
    reason_kind: str = ""                      # branch / out_of_control / world_gap
    reason: str = ""
    pending: bool = False


class DirectorState(BaseModel):
    tension: float = 0.0                       # 张力指数 0-100(独立评估,§6.2)
    tension_trend: str = "flat"                # up/down/flat
    last_conflict_turn: int = 0
    hint: DirectorHint = Field(default_factory=DirectorHint)
    raise_request: RaiseRequest = Field(default_factory=RaiseRequest)
    raise_emitted: bool = False                    # 是否已举手过(避免每回合重复举手)
    ending_options: list[str] = Field(default_factory=list)  # 显式结局状态集合(§6.3)
    converged: bool = False
    ending_selected: str = ""
    injected_events: list[str] = Field(default_factory=list)  # 本回合注入事件文案


# --------------------------------------------------------------------------- 护栏统计 (§7)
class GuardStats(BaseModel):
    last_turn_blocks: int = 0
    total_intercepts: int = 0
    fuse_counts: dict[str, int] = Field(default_factory=dict)   # 层 -> 熔断数
    last_intercept_reason: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- 顶层黑板（编排器持有）
class SimulationState(BaseModel):
    """回合循环的"黑板"对象。LangGraph State 里用单个 `sim` 字段持有它，
    节点内原地修改(blackboard 模式)，避免 reducer 合并复杂度。"""

    scenario: str = ""
    world: WorldState = Field(default_factory=WorldState)
    characters: dict[str, CharacterCard] = Field(default_factory=dict)
    beliefs: dict[str, list[Belief]] = Field(default_factory=dict)  # char_id -> []
    events: list[Event] = Field(default_factory=list)
    director: DirectorState = Field(default_factory=DirectorState)
    guard: GuardStats = Field(default_factory=GuardStats)
    action_order: list[str] = Field(default_factory=list)  # 本回合主动权顺序
    # ---- 思考/决策中间产物（角色思考层写入，供 SSE 与护栏临时读取，非持久化终态）----
    scratch: dict[str, Any] = Field(default_factory=dict)
    ended: bool = False

    def next_event_id(self) -> str:
        return f"E-{len(self.events) + 1:03d}"

    def next_fact_id(self) -> str:
        return f"F-{len(self.world.facts) + 1:03d}"

    def active_facts(self) -> list[Fact]:
        return [f for f in self.world.facts if f.active]