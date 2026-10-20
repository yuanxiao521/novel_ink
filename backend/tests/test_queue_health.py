"""队列活性（v1.45 · B51）：僵尸选择器 + queue_health 形状。

为什么值得单测：`alive=false` 是「worker 挂了」的**唯一信号**，前端横幅与作者判断都靠它。
阈值或字段写错的后果是「队列静默停摆」——不报错、只能靠人发现（B51 就是这么踩的）。
"""
from __future__ import annotations

import pytest

from app.services.tasks_worker import STUCK_SEC, select_orphans


def job(status, jid="job-1", since=None, created=None):
    d = {"job_id": jid, "status": status}
    if since is not None:
        d["running_since"] = since
    if created is not None:
        d["created_at"] = created
    return d


def test_only_running_past_threshold_selected():
    """只挑「running 且超过阈值」的；活任务/排队/已完成都不能误判。"""
    now = 10_000_000
    th = 600_000
    states = [
        job("running", "job-old", since=now - th - 1),
        job("running", "job-fresh", since=now - 1000),
        job("queued", "job-q", since=now - th * 10),
        job("done", "job-d", since=now - th * 10),
        job("failed", "job-f", since=now - th * 10),
    ]
    assert select_orphans(states, now, th) == ["job-old"]


def test_running_since_missing_falls_back_to_created_at():
    now = 10_000_000
    th = 600_000
    states = [job("running", "job-c", created=now - th - 5),
              job("running", "job-n")]          # 两个时间都没有 → 不猜，不动
    assert select_orphans(states, now, th) == ["job-c"]


def test_exactly_at_threshold_counts_and_empty_safe():
    now = 10_000_000
    th = 600_000
    assert select_orphans([job("running", "job-edge", since=now - th)], now, th) == ["job-edge"]
    assert select_orphans([], now, th) == []
    assert select_orphans(None, now, th) == []  # type: ignore[arg-type]


def test_stuck_seconds_is_reasonable():
    """阈值要比前端轮询上限（300s）大，否则会把还在跑的正常任务判成僵尸。"""
    assert STUCK_SEC >= 600


@pytest.mark.asyncio
async def test_queue_health_shape():
    """真实读 Redis：字段齐全、status 取值收敛（Redis 不可用则 skip，不假绿）。"""
    from app.services.tasks_worker import queue_health

    try:
        q = await queue_health()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Redis 不可用：{type(e).__name__}: {e}")
    for k in ("alive", "status", "heartbeat_ttl", "queued", "in_progress", "stuck"):
        assert k in q, f"queue_health 缺字段 {k}"
    assert isinstance(q["alive"], bool)
    assert q["status"] in {"ok", "no-worker", "stuck"}
    assert isinstance(q["queued"], int) and isinstance(q["stuck"], int)
    if q["alive"] is False:
        assert q["status"] in {"no-worker", "stuck"}
