"""ToolExecutor：工具的**唯一落地点**。

顺序：未知工具 → agent 白名单 → 参数校验（0-token）→ 确认闸门 → 执行 → 审计 → 结果。
审计写在**同一套 prose_notes** 里（kind="tool"，created_by 记"谁发起"），
所以"点按钮"和"让责编做"在事后可分辨、可追溯。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from app.services.agents.registry import get_tool
from app.services.agents.spec import can_use, needs_confirm


class ToolDenied(Exception):
    """工具被拒（未知 / 越权 / 缺参 / 未确认）——调用方必须显式处理，不静默。"""


@dataclass
class ToolResult:
    tool: str
    ok: bool
    side_effect: str
    data: dict
    note_id: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "tool": self.tool, "ok": self.ok, "side_effect": self.side_effect,
            "note_id": self.note_id, "error": self.error,
            "data": self.data,
        }


async def call_tool(
    service,
    agent_id: str,
    tool: str,
    args: dict | None = None,
    who: str = "author",
    confirm: bool = False,
    scene_id: str = "",
) -> ToolResult:
    args = dict(args or {})
    decl = get_tool(tool)
    if decl is None:
        raise ToolDenied(f"未知工具：{tool}")
    if not can_use(agent_id, tool):
        raise ToolDenied(f"{agent_id} 无权调用 {tool}（不在其 AgentSpec.tools 白名单）")
    if scene_id and "scene_id" in decl.params and not args.get("scene_id"):
        args["scene_id"] = scene_id
    # "谁发起"由 executor 提供（调用方身份），工具不必自己传
    if "from_agent" in (*decl.params, *decl.optional) and not args.get("from_agent"):
        args["from_agent"] = agent_id or "author"
    missing = [p for p in decl.params if not args.get(p)]
    if missing:
        raise ToolDenied(f"缺少必需参数：{', '.join(missing)}")
    if (decl.needs_confirm or needs_confirm(agent_id, tool)) and not confirm:
        raise ToolDenied(f"{tool} 是破坏性操作，需要作者确认")

    kwargs = {k: args[k] for k in (*decl.params, *decl.optional) if k in args}
    handler = decl.handler
    try:
        if handler.startswith("repo."):
            fn = getattr(service.repo, handler[5:])
        else:
            fn = getattr(service, handler)
        data = await fn(**kwargs)
    except ToolDenied:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolDenied(f"{tool} 执行失败：{e}") from e

    sid = str(args.get("scene_id") or scene_id or "")
    note_id = ""
    if sid:
        note_id = f"note-{uuid.uuid4().hex[:10]}"
        try:
            await service.repo.save_prose_note({
                "id": note_id, "scene_id": sid, "kind": "tool", "status": "approved",
                "suggestion": f"工具 {tool} · 发起 {who}",
                "before": "", "after": "",
                "created_by": who[:16],
                "payload_json": json.dumps({"tool": tool, "args_keys": sorted(kwargs.keys())}, ensure_ascii=False),
                "ts": int(time.time() * 1000),
            })
        except Exception:  # noqa: BLE001  审计失败不该让业务失败（但会被 T9 记为降级）
            note_id = ""
    return ToolResult(tool=tool, ok=True, side_effect=decl.side_effect,
                      data=data if isinstance(data, dict) else {"result": data}, note_id=note_id)
