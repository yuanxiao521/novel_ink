"""API 集成冒烟（内存态 + TestClient）：验证端点连通，无 DB 也可测。

依赖覆盖：把 deps.get_repo 换成 use_db=False 的内存态 Repo（不连真实 PG）。
TestClient 直接驱动 async 路由（Starlette 内部 run_sync 处理）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    from app.db.repo import Repo
    from app.api.deps import get_repo
    from app.main import app

    # 模块级共享内存态 Repo：跨请求共享 _mem，start 后 step/stream 能取到同一 sim
    shared = Repo(use_db=False)

    async def _fake_repo():
        return shared

    app.dependency_overrides[get_repo] = _fake_repo

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_books_empty_in_memory(client):
    """内存态无 seed → /books 返回空列表（端点连通性冒烟）。"""
    r = client.get("/api/v1/books")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_start_step_state_flow(client):
    r = client.post(
        "/api/v1/sims",
        json={"scene_id": "scene-betrayal-night", "resume": False},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["sim_id"]

    r = client.post(f"/api/v1/sims/{sid}/step", params={"n": 1})
    assert r.status_code == 200
    sim = r.json()
    assert sim["turn"] >= 1
    assert "book_id" in sim and "scene_id" in sim

    r = client.get(f"/api/v1/sims/{sid}/state")
    assert r.status_code == 200
    body = r.json()
    assert body["turn"] >= 1
    assert "tension" in body


def test_sim_stream_sse(client):
    r = client.post(
        "/api/v1/sims",
        json={"scene_id": "scene-betrayal-night", "resume": False},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["sim_id"]

    # 限制只推进 1 回合，避免流太长；仍应推流并正常结束
    with client.stream("GET", f"/api/v1/sims/{sid}/stream", params={"turns": 1}) as resp:
        assert resp.status_code == 200, resp.read()
        text = "\n".join(resp.iter_lines())

    # SSE 帧基本格式：有事件名、有 data 载荷、含 done 收尾
    assert "event: " in text
    assert "data: " in text
    assert "event: turn_start" in text
    assert "event: director" in text
    assert "event: prose" in text
    assert "event: done" in text


def test_chapter_detail_lookup(client):
    """导演台反查链路：GET /chapters/{id} 应返回章信息（含 book_id 供反查书树）。

    回归 bug：DirectorPage 从 scene → chapter 反查 book 时调用该端点，
    但后端此前未暴露 → 无 book_id 上下文进入导演台时书标题/树缺失。
    """
    # 建书 → 建章
    r = client.post("/api/v1/books", json={"title": "反查书", "genre": "玄幻"})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    r = client.post(f"/api/v1/books/{bid}/chapters", json={"title": "第一章", "order_no": 1})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # 核心断言：章节详情端点存在并返回 book_id
    r = client.get(f"/api/v1/chapters/{cid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == cid
    assert body["book_id"] == bid
    assert body["title"] == "第一章"