"""责编（场景级副驾）：感知包 → 计划式工具调用 → ToolExecutor 执行 → 汇报。

设计依据：docs/正文协作副驾与Agent协作架构.md（§6 感知包 / §7 工具链）。
落地约束：LLM 客户端只有 `call_strong(prompt, json_schema)`，**没有 function calling**
→ 走「计划式工具调用」：模型只输出 `{reply, actions:[{tool,args,why}]}`，由本地 ToolExecutor 执行。
好处：可测（FakeLLM 可全绿）· 可审计（每次调用落 prose_notes）· 与按钮共用同一实现。
"""
from __future__ import annotations

import json
import re

from app.services.agents.context import render
from app.services.agents.executor import ToolDenied, call_tool
from app.services.agents.perceive import draft_text, perceive
from app.services.agents.perceive import packet_summary as perceive_summary
from app.services.agents.registry import TOOLS
from app.services.agents.spec import EDITOR
from app.services.engine.schema_retry import validate_and_retry
from app.services.llm.client import client as llm_client  # 模块级引用：便于测试统一替换

MAX_ACTIONS = 4

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                    "why": {"type": "string"},
                },
                "required": ["tool"],
            },
        },
    },
    "required": ["reply"],
}


def _validate_plan(raw: object) -> str | None:
    """0-token 校验（契约：返回 None = 通过；返回字符串 = 错误描述，拼回 prompt 重生成）。"""
    if not isinstance(raw, dict):
        return "输出必须是 JSON 对象"
    reply = raw.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return "reply 必须是非空字符串"
    acts = raw.get("actions")
    if acts is not None and not isinstance(acts, list):
        return "actions 必须是数组"
    for i, a in enumerate(acts or []):
        if not isinstance(a, dict) or not isinstance(a.get("tool"), str):
            return f"actions[{i}].tool 必须是非空字符串"
    return None


def _prune_actions(raw_actions: list, executed: list[dict]) -> list[dict]:
    """白名单裁剪：不在 AgentSpec 里的工具**不静默丢弃**，记一条跳过原因给作者。"""
    allow = set(EDITOR.tools)
    out: list[dict] = []
    for a in (raw_actions or [])[:MAX_ACTIONS]:
        if a.get("tool") in allow:
            out.append({
                "tool": a["tool"],
                "args": a.get("args") if isinstance(a.get("args"), dict) else {},
                "why": a.get("why") if isinstance(a.get("why"), str) else "",
            })
        else:
            executed.append({
                "tool": str(a.get("tool")), "ok": False, "side_effect": "?",
                "error": "不在责编白名单（AgentSpec.tools），已跳过", "why": a.get("why", ""),
            })
    return out


def para_index_of(text: str, quote: str) -> int:
    """0-token 辅助：按空行切段后定位引用原文所在段（1-based；找不到 → 0）。"""
    if not quote:
        return 0
    for i, para in enumerate(re.split(r"\n\s*\n", text or ""), start=1):
        if quote.strip() and quote.strip() in para:
            return i
    return 0


async def perceive_scene(repo, scene_id: str) -> dict:
    """责编的感知包（B 批）：统一走 perceive(scope="scene")；行为等价，多带**书级约束块**。"""
    return await perceive(repo, "scene", scene_id=scene_id)


def packet_draft(packet: dict) -> str:
    """当前正文（供工具参数回填，避免模型重复传长文本）。"""
    return draft_text(packet)


def packet_summary(packet: dict) -> dict:
    return perceive_summary(packet)


def render_editor_prompt(packet: dict, message: str, text: str) -> str:
    """感知包 → prompt（B 批：统一上下文装配器 persona=editor；工具清单与输出契约仍归责编）。"""
    tool_lines = [
        f"- {t.name}（{t.side_effect}{'·需确认' if t.needs_confirm else ''}）：{t.desc}"
        for t in TOOLS.values()
    ]
    body = render(packet, "editor")
    tail = [
        "【当前正文】" + (text if text.strip() else "（空）"),
        "【作者要求】" + (message.strip() or "（无，按你的判断）"),
        "【可用工具】" + chr(10) + chr(10).join(tool_lines),
        "【输出】只输出 JSON：{\"reply\": \"给作者的中文说明\", \"actions\": [{\"tool\": \"工具名\", \"args\": {}, \"why\": \"为什么\"}]}。"
        "规则：① 不擅长/无必要时 actions 留空，只解释；② 破坏性工具（保存/改批注状态）不要直接调，"
        "在 reply 里请作者在界面确认；③ 一次最多 4 个动作；④ 不要编造工具名；"
        "⑤ 不要预述工具的执行结果（结果由界面另行展示，你只说明打算做什么）。",
    ]
    sep = chr(10) + chr(10)
    return body + sep + sep.join(tail)
async def editor_chat(service, scene_id: str, message: str, text: str = "", who: str = "author") -> dict:
    """责编对话：感知 → 计划 → 执行（同一工具链）→ 汇报。"""
    packet = await perceive_scene(service.repo, scene_id)
    summary = packet_summary(packet)

    if not getattr(llm_client, "available", False):
        return {
            "reply": "（模型未接入：可以直接在下面点「写手·初稿 / 体检 / 润色 / 质检 / AI 味扫描」，"
                     "这些按钮和我说的是同一套工具；配置好 LLM 后我就能替你连起来做。）",
            "actions": [], "executed": [], "percept": summary,
        }

    try:
        plan = await validate_and_retry(llm_client, render_editor_prompt(packet, message, text), PLAN_SCHEMA, _validate_plan)
    except ValueError as e:
        return {"reply": f"（这次没生成有效计划：{e}；可直接点按钮执行）", "actions": [], "executed": [], "percept": summary}
    if not isinstance(plan, dict):
        return {"reply": "（这次没生成有效计划，可重试；或直接点按钮执行）", "actions": [], "executed": [], "percept": summary}

    executed: list[dict] = []
    actions = _prune_actions(plan.get("actions") or [], executed)
    draft = (text or "").strip() or packet_draft(packet)
    for act in actions:
        # 正文类工具不必让模型复述长文本：模型没给就回填"它对这段的感知"
        decl = TOOLS.get(act["tool"])
        if decl and "text" in decl.params and not act["args"].get("text"):
            act["args"]["text"] = draft
        tool = act["tool"]
        try:
            res = await call_tool(
                service, EDITOR.id, tool, act.get("args") or {},
                who=f"{who}:editor" if who else "editor", confirm=False, scene_id=scene_id,
            )
            executed.append({**res.to_dict(), "why": act.get("why", "")})
        except ToolDenied as e:
            # 破坏性工具需确认 → 不静默失败，如实回报给作者
            executed.append({
                "tool": tool, "ok": False, "side_effect": TOOLS[tool].side_effect if tool in TOOLS else "?",
                "error": str(e), "why": act.get("why", ""),
            })
    return {"reply": plan.get("reply", ""), "actions": actions, "executed": executed, "percept": summary}
