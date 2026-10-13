"""责编（场景级副驾）：感知包 → 计划式工具调用 → ToolExecutor 执行 → 汇报。

设计依据：docs/正文协作副驾与Agent协作架构.md（§6 感知包 / §7 工具链）。
落地约束：LLM 客户端只有 `call_strong(prompt, json_schema)`，**没有 function calling**
→ 走「计划式工具调用」：模型只输出 `{reply, actions:[{tool,args,why}]}`，由本地 ToolExecutor 执行。
好处：可测（FakeLLM 可全绿）· 可审计（每次调用落 prose_notes）· 与按钮共用同一实现。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from app.services.agents.context import render
from app.services.agents.executor import ToolDenied, call_tool
from app.services.agents.perceive import draft_text, perceive
from app.services.agents.perceive import packet_summary as perceive_summary
from app.services.agents.registry import TOOLS
from app.services.agents.spec import EDITOR
from app.services.engine.schema_retry import validate_and_retry
from app.services.llm.client import client as llm_client  # 模块级引用：便于测试统一替换

MAX_ACTIONS = 4

logger = logging.getLogger(__name__)

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

def _summarize(tool: str, data: dict) -> str:
    """把工具产出压成一句「干了什么、结论是什么」（v1.43）。

    动机：作者反馈"点了体检/质检，只看到一句完成，不知道到底查了什么、结论是什么"。
    按钮路径与对话路径共用这一份口径，避免两处各写一套摘要、迟早不一致。
    """
    d = data or {}
    if tool == "prose.review":
        rep = d.get("report") or {}
        issues = rep.get("issues") or []
        voice = rep.get("voice_findings") or []
        high = len([i for i in issues if str((i or {}).get("severity")) == "high"])
        return f"体检：{len(issues)} 条问题（高危 {high}）· 口吻 {len(voice)} 处"
    if tool == "prose.verify":
        op = d.get("opinion") or {}
        return ("质检：伏笔推进 %d · 信念变化 %d · 因果 %d · 状态变化 %d · 风险 %d"
                % (len(op.get("foreshadow_updates") or []), len(op.get("belief_deltas") or []),
                   len(op.get("causal") or []), len(op.get("state_deltas") or []),
                   len(op.get("risks") or [])))
    if tool == "prose.polish":
        return "润色：产出 %d 字候选 · %s" % (len(str(d.get("after") or "")), str(d.get("summary") or "")[:60])
    if tool == "prose.quality_score":
        total = d.get("total")
        if total is None:
            return "评审：正文为空，未评分"
        return f"评审：{total} 分 · 弱项 {len(d.get('weak_points') or [])} 条"
    if tool == "prose.scan_tone":
        rules = ((d.get("report") or {}).get("rules")) or []
        return f"AI 味扫描：命中 {len(rules)} 条（0-token 规则）"
    if tool == "prose.spot_fix":
        applied = [c for c in (d.get("changes") or []) if (c or {}).get("applied")]
        if d.get("accepted") and applied:
            return "定点修复：改 %d 句（命中 %s→%s）" % (len(applied), d.get("hits_before"), d.get("hits_after"))
        return "定点修复：未采纳（%s）" % (d.get("reason") or d.get("skipped") or "双闸未通过")
    if tool == "prose.quality_loop":
        if d.get("accepted"):
            return "质量回环：%s → %s 分（%s 路由，改 %d 处）" % (
                (d.get("before") or {}).get("total"), (d.get("after_score") or {}).get("total"),
                d.get("route"), len([c for c in (d.get("changes") or []) if (c or {}).get("applied")]))
        return "质量回环：未采纳（%s）" % (d.get("reason") or d.get("skipped") or "评分达标或未改动")
    if tool == "prose.draft":
        return f"写手：产出初稿 {len(str(d.get('text') or ''))} 字"
    if tool == "prose.review_meeting":
        return "审稿会：" + str(d.get("verdict") or "")[:80]
    if tool == "prose.save":
        return f"保存：{len(str(d.get('text') or ''))} 字已落库"
    cand = d.get("text") or d.get("after") or d.get("candidate")
    if isinstance(cand, str) and cand.strip():
        return f"产出候选 {len(cand)} 字"
    if d.get("report") is not None:
        return f"发现 {len((d.get('report') or {}).get('issues') or [])} 条问题"
    return ""


# ---------------------------------------------------------------- 流式版（v1.39：让作者看见责编在干什么）
async def editor_chat_stream(service, scene_id: str, message: str, text: str = "", who: str = "author"):
    """editor_chat 的逐阶段流式版：感知 → 计划 → 逐工具执行 → 完成。

    每个阶段 yield 一个 dict 事件（路由层转 SSE）——作者不再盯着
    "（责编在想…）"干等 10-30 秒，而是看到：
      percept → 「读到正文 N 字 · 批注 M 条」
      plan    → 「打算：体检 → 润色」（模型计划）
      tool_start / tool_done（逐个工具，含 ok/denied 与结果摘要）
      reply   → 最终说明
      done    → 收尾（含 percept 汇总）
    非 LLM 场景（未接入/计划失败）也走同一套事件，前端无需分支。
    """
    packet = await perceive_scene(service.repo, scene_id)
    summary = packet_summary(packet)
    yield {"event": "percept", "data": summary}

    if not getattr(llm_client, "available", False):
        yield {"event": "reply", "data": {"reply": "（模型未接入：可以直接点「写手·初稿 / 体检 / 润色 / 质检」按钮，这些按钮和我说的是同一套工具。）"}}
        yield {"event": "done", "data": {"percept": summary, "executed": []}}
        return

    try:
        plan = await validate_and_retry(llm_client, render_editor_prompt(packet, message, text), PLAN_SCHEMA, _validate_plan)
    except ValueError as e:
        yield {"event": "reply", "data": {"reply": f"（这次没生成有效计划：{e}；可直接点按钮执行）"}}
        yield {"event": "done", "data": {"percept": summary, "executed": []}}
        return
    if not isinstance(plan, dict):
        yield {"event": "reply", "data": {"reply": "（这次没生成有效计划，可重试；或直接点按钮执行）"}}
        yield {"event": "done", "data": {"percept": summary, "executed": []}}
        return

    actions = plan.get("actions") or []
    yield {"event": "plan", "data": {"reply": plan.get("reply", ""), "actions": actions}}

    executed: list[dict] = []
    pruned = _prune_actions(actions, executed)
    draft = (text or "").strip() or packet_draft(packet)
    for act in pruned:
        decl = TOOLS.get(act["tool"])
        if decl and "text" in decl.params and not act["args"].get("text"):
            act["args"]["text"] = draft
        tool = act["tool"]
        why = act.get("why", "")
        yield {"event": "tool_start", "data": {"tool": tool, "why": why}}
        try:
            res = await call_tool(
                service, EDITOR.id, tool, act.get("args") or {},
                who=f"{who}:editor" if who else "editor", confirm=False, scene_id=scene_id,
            )
            executed.append({**res.to_dict(), "why": why})
            # v1.43：摘要按工具类型给「查了什么、结论是什么」，与按钮路径共用 _summarize
            yield {"event": "tool_done", "data": {"tool": tool, "ok": True,
                   "summary": _summarize(tool, res.data), "result": res.to_dict(), "why": why}}
        except ToolDenied as e:
            executed.append({
                "tool": tool, "ok": False, "side_effect": TOOLS[tool].side_effect if tool in TOOLS else "?",
                "error": str(e), "why": why,
            })
            yield {"event": "tool_done", "data": {"tool": tool, "ok": False, "denied": True,
                   "error": str(e), "why": why}}
        except Exception as e:  # noqa: BLE001  单个工具炸了不能让整条流静默断掉
            logger.exception("[editor] 工具执行异常 tool=%s", tool)
            executed.append({"tool": tool, "ok": False,
                             "error": f"{type(e).__name__}: {e}", "why": why})
            yield {"event": "tool_done", "data": {"tool": tool, "ok": False,
                   "error": f"{type(e).__name__}: {e}", "why": why}}

    yield {"event": "reply", "data": {"reply": plan.get("reply", "")}}
    yield {"event": "done", "data": {"percept": summary, "executed": executed}}


# ---------------------------------------------------------------- 按钮直调（v1.43：复杂按钮也看得见过程）
_TICK_SEC = 2.0     # 心跳间隔：长工具（体检 ~50s / 质检 ~40s）也要让作者看到"还在跑、跑了多久"


async def tool_run_stream(service, scene_id: str, tool: str, args: dict | None = None,
                          who: str = "author", confirm: bool = False):
    """**按钮**直调工具的流式版（v1.43）：与责编对话同一套事件词汇。

    动机（用户实测反馈）："审稿会/体检/质检这些复杂按钮点了没有输出，也不知道怎么体检的"。
    根因：按钮走同步 POST，结果只落到右侧「质检」tab——作者停在「责编」tab 时什么都看不到；
    且结果卡只显示摘要，看不到明细。

    事件序列（前端与对话路径共用同一张过程卡）：
      tool_start → tick（每 2s 一次耗时心跳）→ tool_done（含 summary + 完整 result）→ done
    被拒（ToolDenied）不静默：tool_done ok=false + denied=true，前端弹确认后带 confirm 重试。

    连接断开**不取消**工具：LLM 已经花掉的钱要变成审计记录，不能白花。
    """
    decl = TOOLS.get(tool)
    if decl is None:
        yield {"event": "tool_done", "data": {"tool": tool, "ok": False, "error": f"未知工具：{tool}"}}
        yield {"event": "done", "data": {"executed": []}}
        return

    yield {"event": "tool_start", "data": {"tool": tool, "why": decl.desc}}
    t0 = time.time()
    task = asyncio.ensure_future(call_tool(
        service, EDITOR.id, tool, args or {},
        who=f"{who}:editor" if who else "editor", confirm=confirm, scene_id=scene_id,
    ))
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=_TICK_SEC)
            if done:
                break
            yield {"event": "tick", "data": {"tool": tool, "elapsed": round(time.time() - t0, 1)}}
        res = task.result()
    except ToolDenied as e:
        yield {"event": "tool_done", "data": {"tool": tool, "ok": False, "denied": True,
               "error": str(e), "elapsed": round(time.time() - t0, 1)}}
        yield {"event": "done", "data": {"executed": []}}
        return
    except Exception as e:  # noqa: BLE001  异常必须下发，前端不能停在"处理中"
        logger.exception("[editor] 按钮工具异常 tool=%s", tool)
        yield {"event": "tool_done", "data": {"tool": tool, "ok": False,
               "error": f"{type(e).__name__}: {e}", "elapsed": round(time.time() - t0, 1)}}
        yield {"event": "done", "data": {"executed": []}}
        return

    payload = res.to_dict()
    yield {"event": "tool_done", "data": {
        "tool": tool, "ok": True, "summary": _summarize(tool, res.data),
        "result": payload, "elapsed": round(time.time() - t0, 1),
    }}
    yield {"event": "done", "data": {"executed": [payload]}}


# ---------------------------------------------------------------- 主笔（v1.40）：计划式工具对话
_CHIEF_PLAN_SCHEMA = {
    "reply": "str 给作者的中文说明",
    "actions": [{"tool": "str 工具名", "args": {}, "why": "str 为什么"}],
}

def _validate_chief_plan(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return "顶层必须是 JSON 对象"
    if not str(raw.get("reply") or "").strip():
        return "缺少 reply（给作者的说明）"
    acts = raw.get("actions")
    if not isinstance(acts, list):
        return "actions 必须是数组（可为空数组）"
    for i, a in enumerate(acts):
        if not isinstance(a, dict) or not str(a.get("tool") or "").strip():
            return f"actions[{i}] 缺 tool"
    return None

_CHIEF_TOOLS_PROMPT_HEAD = """你是小说【主笔】（总编剧），掌管这本书的结构与约束：书树、节奏、伏笔、角色弧、
书级约束表与记忆。你**不直接改正文**（那是责编的活）。

你可以调用以下工具（会经统一执行器校验与审计）："""

_CHIEF_TOOLS_PROMPT_TAIL = """

【作者要求】{message}
【输出】只输出 JSON：{{"reply": "给作者的中文说明", "actions": [{{"tool": "工具名", "args": {{}}, "why": "为什么"}}]}}。
规则：① 不需要执行工具时 actions 留空，只解释或建议；② outline.commit 是破坏性操作，
系统会先出预览再由作者确认——你的 actions 里可以带 outline.plan（出预览），
不要直接带 outline.commit（作者在界面上确认）；③ 一次最多 3 个动作；④ 不要编造工具名。"""

async def chief_chat_with_tools(service, book_id: str, message: str, who: str = "author") -> dict:
    """主笔的计划式工具对话（v1.40）：感知 → 计划 → 经 ToolExecutor 执行 → 汇报。

    与责编 editor_chat 同一模式；工具集是主笔的四个（outline.plan/characters.generate/
    memory.write/outline.commit），感知是 chief_perceive 的文本（书级）。
    """
    perceived = await service.chief_perceive(book_id)
    tool_lines = [
        f"- {t.name}（{t.side_effect}{'·需确认' if t.needs_confirm else ''}）：{t.desc}"
        for t in TOOLS.values() if t.group == "chief"
    ]
    prompt = (
        _CHIEF_TOOLS_PROMPT_HEAD + chr(10) + chr(10).join(tool_lines)
        + _CHIEF_TOOLS_PROMPT_TAIL.format(message=message.strip() or "（无，按你的判断给建议）")
        + "\n\n【当前书的感知上下文】\n" + (perceived or "（暂无）")
    )
    executed: list[dict] = []

    if not getattr(llm_client, "available", False):
        return {
            "reply": "（模型未接入：排骨架/生成角色卡可以直接点界面上按钮；接入后我就能在对话里替你连起来做。）",
            "executed": [],
        }

    try:
        plan = await validate_and_retry(llm_client, prompt, _CHIEF_PLAN_SCHEMA, _validate_chief_plan)
    except ValueError as e:
        return {"reply": f"（这次没生成有效计划：{e}）", "executed": []}
    if not isinstance(plan, dict):
        return {"reply": "（这次没生成有效计划，可重试）", "executed": []}

    for act in (plan.get("actions") or [])[:3]:
        tool = str(act.get("tool") or "")
        args = dict(act.get("args") or {})
        args.setdefault("book_id", book_id)
        # 主笔的破坏性动作一律升到作者确认（outline.commit 由界面预览流负责，这里直接拒）
        if tool == "outline.commit":
            executed.append({"tool": tool, "ok": False,
                             "error": "骨架落库由作者在预览卡上确认（先 outline.plan 出预览）", "why": act.get("why", "")})
            continue
        try:
            res = await call_tool(
                service, "chief", tool, args,
                who=f"{who}:chief" if who else "chief", confirm=True, book_id=book_id,
            )
            executed.append({**res.to_dict(), "why": act.get("why", "")})
        except ToolDenied as e:
            executed.append({"tool": tool, "ok": False, "error": str(e), "why": act.get("why", "")})

    return {"reply": plan.get("reply", ""), "executed": executed}
