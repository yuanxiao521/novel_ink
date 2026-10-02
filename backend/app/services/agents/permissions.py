"""写权限矩阵（**草案** · A+ 只出草案不强制）。

设计依据：docs/正文协作副驾与Agent协作架构.md §5.2。
规则：**每个字段只有一个 owner**（防多写者漂移）；这里只声明与展示，尚未在写入路径强制。
启用时机：B 批（约束表全局化）之后统一接进 ToolExecutor 的写校验。
"""
from __future__ import annotations

# target → (唯一写者, 说明)
FIELD_OWNERS: dict[str, tuple[str, str]] = {
    "chapters": ("chief", "章节结构：主笔规划产物（作者经 UI 可改）"),
    "scenes:structure": ("chief", "场景结构/舞台/目标：主笔产物（作者可改）"),
    "scenes:final_prose": ("editor", "正文：责编/作者写入（写手只出候选）"),
    "characters": ("chief", "角色卡：主笔生成（作者可改）"),
    "book_memories": ("chief", "方向/约束/记忆：主笔与作者维护"),
    "beliefs": ("bookkeeping", "信念账本：记账 agent 唯一写入"),
    "world_states": ("bookkeeping", "世界状态：记账 agent 唯一写入"),
    "foreshadows": ("bookkeeping", "伏笔三态：记账 agent 唯一写入"),
    "prose_notes": ("executor", "审计：ToolExecutor 唯一写入（含谁发起）"),
    "prose_annotations": ("author", "批注：作者创建；工具只能改状态"),
    "agent_tasks": ("initiator", "任务单：发起者写，编排者改状态"),
    "world.facts": ("round:refresh_world", "黑板事实：回合内唯一写者"),
}


def matrix() -> list[dict]:
    return [
        {"target": k, "owner": v[0], "note": v[1], "enforced": False}
        for k, v in FIELD_OWNERS.items()
    ]
