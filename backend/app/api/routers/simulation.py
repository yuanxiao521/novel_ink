"""Api 层 · simulation 路由：启动 / step / 状态 / 介入 / SSE 流式。

数据库会话经 `Depends` 注入（见 `app.api.deps`），路由不自行握手。
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_service
from app.config import settings
from app.schemas import models
from app.services.service import SimulationService

router = APIRouter(prefix="/api/v1", tags=["simulation"])

# SSE 节流：回合间步进节奏
_STREAM_INTERVAL = 0.25


def _sse(event: str, data: dict) -> str:
    """把一条事件序列化成 SSE 帧（event: 事件名\n data: JSON\n\n）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sim_stream(
    sim_id: str,
    svc: SimulationService,
    turns: Optional[int],
) -> AsyncGenerator[str, None]:
    """长连接生成器：逐步推进回合并按阶段推送 SSE。

    每回合阻塞同步调用 svc.step(单回合，很快)，随后读最新 sim，从 sim.events
    尾部抽取本回合增量。客户端断连以 asyncio.CancelledError 传入生成器，向上
    抛出即可终止（StreamingResponse 处理取消）。
    """
    sim = svc.get_state(sim_id)
    prev_event_count = len(sim.events)
    done = 0
    try:
        while True:
            # 终止条件：结束 / 收敛 / 举手待批 / 超回合上限 / （可选）固定回合数
            if (
                sim.ended
                or sim.director.converged
                or sim.director.raise_request.pending
                or sim.world.turn >= settings.max_turns
            ):
                break
            if turns is not None and done >= turns:
                break

            turn = sim.world.turn + 1
            yield _sse("turn_start", {"turn": turn})

            # 推进一个回合（单回合图，结束态在 render_prose 写回 sim.ended）
            svc.step(sim_id, 1)
            sim = svc.get_state(sim_id)
            new_events = sim.events[prev_event_count:]
            prev_event_count = len(sim.events)

            # 每个角色：感知 → 思考 → 决策 → 行动（取 scratch 中间产物 + 本回合新事件）
            thoughts = sim.scratch.get("thoughts", {}) or {}
            decisions = sim.scratch.get("decisions", {}) or {}
            for char_id in sim.action_order:
                act = [
                    e.model_dump()
                    for e in new_events
                    if e.source.value == "character" and e.actor == char_id
                ]
                yield _sse("character_perceive", {"turn": turn, "char_id": char_id})
                yield _sse(
                    "character_think",
                    {"turn": turn, "char_id": char_id, "thought": thoughts.get(char_id)},
                )
                yield _sse(
                    "character_decide",
                    {"turn": turn, "char_id": char_id, "decision": decisions.get(char_id)},
                )
                yield _sse("character_act", {"turn": turn, "char_id": char_id, "events": act})

            # 导演：张力 + hint 摘要 + 举手
            d = sim.director
            yield _sse("director", {
                "turn": turn,
                "tension": d.tension,
                "tension_trend": d.tension_trend,
                "hint": d.hint.stage_prompt,
                "raise_request": d.raise_request.model_dump(),
            })

            # 护栏
            yield _sse("guard", {"turn": turn, "guard": sim.guard.model_dump()})

            # 成文段落
            prose = [
                e for e in new_events
                if e.type.value == "text" and e.source.value == "system"
            ]
            text = prose[-1].payload.get("text", "") if prose else ""
            yield _sse("prose", {"turn": turn, "text": text})

            yield _sse("turn_end", {"turn": turn})
            done += 1
            await asyncio.sleep(_STREAM_INTERVAL)

        yield _sse("done", {
            "turn": sim.world.turn,
            "ended": bool(sim.ended),
            "converged": bool(sim.director.converged),
            "raise_pending": bool(sim.director.raise_request.pending),
        })
    except asyncio.CancelledError:
        # 客户端断连 → 终止流，不做清理（每回合已落库）
        raise


@router.post("/sims", status_code=status.HTTP_201_CREATED)
def start(scenario: str = "betrayal_night", svc: SimulationService = Depends(get_service)):
    """启动一个叙事，返回 sim_id。"""
    try:
        sid = svc.start(scenario)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"sim_id": sid}


@router.post("/sims/{sim_id}/step")
def step(sim_id: str, n: int = 1, svc: SimulationService = Depends(get_service)):
    """推进 n 个回合，返回最新黑板状态。"""
    try:
        sim = svc.step(sim_id, n)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return sim


@router.get("/sims/{sim_id}/state")
def get_state(sim_id: str, svc: SimulationService = Depends(get_service)):
    """查看当前黑板状态（不含敏感中间量）。"""
    try:
        sim = svc.get_state(sim_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "sim_id": sim_id,
        "turn": sim.world.turn,
        "tension": sim.director.tension,
        "tension_trend": sim.director.tension_trend,
        "converged": sim.director.converged,
        "ending": sim.director.ending_selected,
        "raise_pending": sim.director.raise_request.pending,
        "raise_reason": sim.director.raise_request.reason,
        "characters": list(sim.characters.keys()),
        "beliefs": {cid: [b.model_dump() for b in bl] for cid, bl in sim.beliefs.items()},
        "guard": sim.guard.model_dump(),
    }


@router.get("/sims/{sim_id}/stream")
async def stream(
    sim_id: str,
    turns: Optional[int] = None,
    svc: SimulationService = Depends(get_service),
):
    """SSE 长连接：逐步推进回合并按 感知→思考→决策→事件→记忆→成文 分段增量推送。

    供前端导演台 EventSource 消费。连接建立前先校验 sim 存在；随后交给异步
    生成器_循环推进。`turns` 可选：限制推进的回合数（默认跑到 ended/converged/举手）。
    """
    try:
        svc.get_state(sim_id)  # 连接前 404 校验，不抛进生成器
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到模拟")
    return StreamingResponse(
        _sim_stream(sim_id, svc, turns),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 作者介入：举手批复 + 导演工具
class InterveneBody(models.BaseModel):
    action: str                      # accept / reject / inject_event / adjust_weight / expose
    payload: dict = {}               # 视 action 而异


@router.post("/sims/{sim_id}/intervene")
def intervene(sim_id: str, body: InterveneBody, svc: SimulationService = Depends(get_service)):
    try:
        sim = svc.intervene(sim_id, body.action, body.payload)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sim_id": sim_id, "action": body.action, "ok": True, "turn": sim.world.turn}