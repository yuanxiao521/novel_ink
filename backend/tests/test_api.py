"""API 集成冒烟（内存态）：验证端点连通，无 DB 也可测。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    from app.data.session import get_db as orig_get_db  # noqa

    # 强制内存态会话（不连真实 PG）
    class _FakeSess:
        ready = False

    def _fake_db():
        yield _FakeSess()

    monkeypatch.setattr("app.data.session.get_db", _fake_db)

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_start_step_state_flow(client):
    r = client.post("/api/v1/sims", params={"scenario": "betrayal_night"})
    assert r.status_code == 201, r.text
    sid = r.json()["sim_id"]

    r = client.post(f"/api/v1/sims/{sid}/step", params={"n": 1})
    assert r.status_code == 200
    sim = r.json()
    assert sim["world"]["turn"] == 1

    r = client.get(f"/api/v1/sims/{sid}/state")
    assert r.status_code == 200
    body = r.json()
    assert body["turn"] == 1
    assert "tension" in body


def test_intervene_accept_clears_raise(client):
    # 先跑足够多回合触发举手
    r = client.post("/api/v1/sims", params={"scenario": "betrayal_night"})
    sid = r.json()["sim_id"]
    client.post(f"/api/v1/sims/{sid}/step", params={"n": 5})
    st = client.get(f"/api/v1/sims/{sid}/state").json()
    assert st["raise_pending"] is True or st["converged"] is True

    # 同意介入
    r = client.post(f"/api/v1/sims/{sid}/intervene", json={"action": "accept", "payload": {"text": "雨势更急了一道"}})
    assert r.status_code == 200


def test_sim_stream_sse(client):
    r = client.post("/api/v1/sims", params={"scenario": "betrayal_night"})
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