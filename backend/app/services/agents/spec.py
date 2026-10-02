"""AgentSpec：agent 能力声明（**纯数据，只声明不执行**）。

设计依据：docs/正文协作副驾与Agent协作架构.md §2.1。
用途：① ToolExecutor 校验"这个 agent 能不能调这个工具"（越权直接拒）；
      ② 前端展示每个 agent 能读/能写什么（不再靠口头约定）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    scope: str                       # book | scene | round | task
    persona: str                     # system prompt 模板名
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    model_tier: str = "strong"       # strong | cheap | none
    emits: tuple[str, ...] = ()
    can_initiate_tasks: bool = False
    needs_confirm: tuple[str, ...] = ()


# 主笔（结构层样板）：掌管结构与约束，**不碰正文**
CHIEF = AgentSpec(
    id="chief", name="主笔", scope="book", persona="chief",
    reads=("book_tree", "memories", "inspirations", "ledgers", "writing_signals"),
    writes=("outline", "characters", "worldview", "memories", "constraints"),
    tools=("outline.commit", "characters.generate", "memory.write"),
    emits=("plan.preview", "outline.committed"),
    can_initiate_tasks=True,
    needs_confirm=("outline.commit",),
)

# 责编（场景层样板 · 新增）：掌管本场文字，**不碰骨架与约束**
EDITOR = AgentSpec(
    id="editor", name="责编", scope="scene", persona="editor",
    reads=("scene", "characters", "annotations", "prose_notes", "ledgers"),
    writes=("final_prose", "annotations"),
    tools=(
        "prose.draft", "prose.review", "prose.polish", "prose.verify",
        "prose.scan_tone", "prose.spot_fix", "prose.quality_loop",
        "prose.save", "annotation.list", "annotation.resolve",
    ),
    emits=("action.plan", "prose.candidate", "prose.saved"),
    can_initiate_tasks=True,
    needs_confirm=("prose.save", "annotation.resolve"),
)

# 角色 agent：只经黑板表达，**不允许发起任务**（硬边界）
CHARACTER = AgentSpec(
    id="character", name="角色", scope="round", persona="character",
    reads=("own_card", "own_beliefs", "perceivable_facts", "recent_events"),
    writes=("thought", "action", "emotion"),
    tools=(),
    emits=("character.thought", "character.action"),
    can_initiate_tasks=False,
)

SPECS: dict[str, AgentSpec] = {s.id: s for s in (CHIEF, EDITOR, CHARACTER)}


def get_spec(agent_id: str) -> AgentSpec | None:
    return SPECS.get(agent_id)


def can_use(agent_id: str, tool: str) -> bool:
    """白名单校验：agent 只能调自己声明的工具（空 agent_id = 系统/作者直调，放行）。"""
    if not agent_id:
        return True
    s = SPECS.get(agent_id)
    return bool(s and tool in s.tools)


def needs_confirm(agent_id: str, tool: str) -> bool:
    s = SPECS.get(agent_id)
    return bool(s and tool in s.needs_confirm)
