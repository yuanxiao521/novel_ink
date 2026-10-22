"""写权限矩阵（v1.30 起**启用**：走工具链的 3 行已强制，其余诚实标注原因）。

设计依据：docs/正文协作副驾与Agent协作架构.md §5.2。
规则：**每个字段只有一个 owner**（防多写者漂移）。

强制分三处（不假装一道闸门管全部）：
  ① ToolExecutor 字段级闸门（check_write）：`scenes:final_prose` / `prose_annotations`；
  ② tasks.py 发起者白名单 + 状态机（A+ 已强制）：`agent_tasks`；
  ③ 引擎/记账路径天然单写（无工具写路径）：账本三行、黑板事实、审计——
     这些行 enforced=False 是**诚实状态**：不是没管，是不经这道闸门（作者编辑走 UI 直调）。
"""
from __future__ import annotations

# target → (唯一写者, 说明)
FIELD_OWNERS: dict[str, tuple[str, str]] = {
    "chapters": ("chief", "章节结构：主笔规划产物（作者经 UI 可改）"),
    "chapters:production": ("chief", "章级制作单：这一章走直写/涌现/混合（主笔裁决，作者可改）"),
    "scenes:structure": ("chief", "场景结构/舞台/目标：主笔产物（作者可改）"),
    "scenes:final_prose": ("editor", "正文：责编/作者写入（写手只出候选）"),
    # v1.65：章正文是**唯一真源**（合并后章级是主线；场正文只是可并入的来源快照）
    "chapters:prose": ("editor", "章正文：责编/作者写入（写手只出候选；场正文并入也走这条）"),
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

# ToolExecutor 字段级闸门：target → 除作者外允许的 agent。
# 作者（agent_id="" 或 "author" = UI 直调/按钮）永远放行——矩阵管的是 agent 越权，不管人类。
# 不在表里的 target = 本闸门不管（由 tasks.py / 引擎路径 / 无工具写路径保证）。
_WRITE_ALLOWED: dict[str, set[str]] = {
    "scenes:final_prose": {"editor"},
    "chapters:prose": {"editor"},      # v1.65：chapter.merge_scenes

    "prose_annotations": {"editor"},   # annotation.resolve 只改状态（作者创建走 UI）
    "chapters": {"chief"},             # outline.commit（v1.40 补：此前主笔走散装 API 不过闸门）
    "chapters:production": {"chief"},  # v1.50：章级制作单（mode/must_rehearse/hook）
    "characters": {"chief"},           # characters.generate
    "book_memories": {"chief"},        # memory.write
}

# 矩阵行 → 强制位置（"" = 未强制，原因见 note）
_ENFORCE_WHERE: dict[str, str] = {
    "scenes:final_prose": "ToolExecutor 字段级闸门",
    "prose_annotations": "ToolExecutor 字段级闸门（作者创建走 UI）",
    "agent_tasks": "tasks.py 发起者白名单 + 状态机",
}

# v1.40：主笔工具治理补齐后，这三行也由 ToolExecutor 强制
_ENFORCE_WHERE["chapters"] = "ToolExecutor 字段级闸门（outline.commit）"
_ENFORCE_WHERE["characters"] = "ToolExecutor 字段级闸门（characters.generate）"
_ENFORCE_WHERE["book_memories"] = "ToolExecutor 字段级闸门（memory.write）"
_ENFORCE_WHERE["chapters:production"] = "ToolExecutor 字段级闸门（chapter.production）"
_ENFORCE_WHERE["chapters:prose"] = "ToolExecutor 字段级闸门（chapter.merge_scenes）"


def check_write(agent_id: str, target: str) -> str | None:
    """字段级写校验：允许返回 None，拒绝返回原因（调用方 ToolDenied 抛出，不静默）。"""
    if not target:
        return None                       # 只读 / 只出候选，不落库
    if agent_id in ("", "author"):
        return None                       # 人类直调 = 作者意志
    allowed = _WRITE_ALLOWED.get(target)
    if allowed is None:
        return None                       # 本闸门不管这行（另有强制位置或无工具写路径）
    if agent_id in allowed:
        return None
    owner = FIELD_OWNERS[target][0]
    return (f"{agent_id} 不能写 {target}（owner={owner}；"
            f"字段级写权限已启用，见 permissions.py）")


def matrix() -> list[dict]:
    return [
        {"target": k, "owner": v[0], "note": v[1],
         "enforced": k in _ENFORCE_WHERE,
         "enforce_where": _ENFORCE_WHERE.get(k, "")}
        for k, v in FIELD_OWNERS.items()
    ]
