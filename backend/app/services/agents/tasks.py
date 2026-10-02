"""任务总线（L3 消息层）：请求 / 裁决 / 通知。

设计依据：docs/正文协作副驾与Agent协作架构.md §5.1/§8.2。
三条纪律：
  ① **只传请求/裁决/通知，不传状态**（状态走黑板或账本，防第二套事实来源）
  ② 状态机：submitted → working → input-required（等作者确认）→ completed / failed
  ③ **发起者硬边界**：只有 作者 / 编排者 / 主笔 / 责编 能发起；**角色 agent 不允许发起任务**
     （双重校验：白名单 + AgentSpec.can_initiate_tasks）
"""
from __future__ import annotations

import json
import time
import uuid

from app.services.agents.spec import get_spec

ALLOWED_INITIATORS: frozenset[str] = frozenset({"author", "orchestrator", "chief", "editor"})
ALLOWED_KINDS: frozenset[str] = frozenset({"rehearsal", "expose", "adjudicate", "rewrite"})
STATUSES: frozenset[str] = frozenset({"submitted", "working", "input-required", "completed", "failed"})
TRANSITIONS: dict[str, set[str]] = {
    "submitted": {"working", "input-required", "failed"},
    "working": {"input-required", "completed", "failed"},
    "input-required": {"working", "completed", "failed"},
    "completed": set(),
    "failed": set(),
}

KIND_LABEL = {
    "rehearsal": "请求彩排",
    "expose": "请求信息曝光",
    "adjudicate": "请求作者裁决",
    "rewrite": "请求重写",
}


class TaskDenied(Exception):
    """任务被拒（发起者越界 / 未知类型 / 非法流转）——调用方必须显式处理，不静默。"""


def check_initiator(agent_id: str) -> None:
    """双重校验：允许名单 + AgentSpec 声明（角色 agent 必被拒）。"""
    if agent_id not in ALLOWED_INITIATORS:
        raise TaskDenied(
            f"{agent_id} 不允许发起任务：只允许 作者/编排者/主笔/责编；"
            "角色 agent 只能通过黑板表达"
        )
    spec = get_spec(agent_id)
    if spec is not None and not spec.can_initiate_tasks:
        raise TaskDenied(f"{spec.name} 的 AgentSpec 声明 can_initiate_tasks=False")


async def create_task(
    repo,
    *,
    from_agent: str,
    to_agent: str = "stage_manager",
    kind: str = "rehearsal",
    goal: str = "",
    scene_id: str = "",
    book_id: str = "",
    input_ref: dict | None = None,
) -> dict:
    check_initiator(from_agent)
    if kind not in ALLOWED_KINDS:
        raise TaskDenied(f"未知任务类型：{kind}（可选 {sorted(ALLOWED_KINDS)}）")
    ts = int(time.time() * 1000)
    task = {
        "id": f"task-{uuid.uuid4().hex[:10]}",
        "book_id": book_id, "scene_id": scene_id,
        "from_agent": from_agent, "to_agent": to_agent, "kind": kind,
        "goal": goal, "input_ref": json.dumps(input_ref or {}, ensure_ascii=False),
        "status": "submitted", "artifact_ref": "{}", "ts": ts,
    }
    await repo.save_task(task)
    await repo.save_message({
        "id": f"msg-{uuid.uuid4().hex[:10]}", "task_id": task["id"], "role": "request",
        "from_agent": from_agent, "to_agent": to_agent,
        "content_json": json.dumps({"kind": kind, "goal": goal}, ensure_ascii=False), "ts": ts,
    })
    return task


async def transition(repo, task_id: str, status: str, note: str = "", artifact_ref: dict | None = None) -> dict:
    if status not in STATUSES:
        raise TaskDenied(f"未知状态：{status}")
    cur = await repo.get_task(task_id)
    if cur is None:
        raise TaskDenied("任务不存在")
    allowed = TRANSITIONS.get(cur["status"], set())
    if status not in allowed:
        raise TaskDenied(f"非法状态流转：{cur['status']} → {status}（允许 {sorted(allowed) or '无（终态）'}）")
    patch: dict = {"status": status}
    if artifact_ref:
        patch["artifact_ref"] = json.dumps(artifact_ref, ensure_ascii=False)
    out = await repo.update_task(task_id, patch)
    await repo.save_message({
        "id": f"msg-{uuid.uuid4().hex[:10]}", "task_id": task_id, "role": "notify",
        "from_agent": cur.get("to_agent", ""), "to_agent": cur.get("from_agent", ""),
        "content_json": json.dumps({"status": status, "note": note}, ensure_ascii=False),
        "ts": int(time.time() * 1000),
    })
    return out or cur


def task_dict(t: dict) -> dict:
    """给前端的形状：把 JSON 字段解开、补中文标签。"""
    def _load(v: str) -> dict:
        try:
            return json.loads(v or "{}")
        except Exception:  # noqa: BLE001
            return {}

    return {
        "id": t.get("id", ""), "book_id": t.get("book_id", ""), "scene_id": t.get("scene_id", ""),
        "from_agent": t.get("from_agent", ""), "to_agent": t.get("to_agent", ""),
        "kind": t.get("kind", ""), "kind_label": KIND_LABEL.get(t.get("kind", ""), t.get("kind", "")),
        "goal": t.get("goal", ""), "status": t.get("status", ""),
        "input_ref": _load(t.get("input_ref", "{}")), "artifact_ref": _load(t.get("artifact_ref", "{}")),
        "ts": t.get("ts", 0), "created_at": t.get("created_at", ""), "updated_at": t.get("updated_at", ""),
    }
