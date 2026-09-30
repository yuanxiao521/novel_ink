"""灵感池接口测试（内存态 + TestClient）：CRUD / 采纳切换 / 主笔生成 fallback / plan 带灵感。

链路对齐计划 AC-2/AC-3/AC-5：字段与前端 InspirationCard TS 类型一一对应。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from app.db.repo import Repo
    from app.api.deps import get_repo
    from app.main import app

    shared = Repo(use_db=False)

    async def _fake_repo():
        return shared

    app.dependency_overrides[get_repo] = _fake_repo

    # module scope：TestClient 只建一次（lifespan 只跑一次迁移），避免每个用例叩真库
    with TestClient(app) as c:
        c._shared_repo = shared  # type: ignore[attr-defined]
        yield c, shared

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh(client):
    """每用例重置内存态并重建测试书（module 级 client 复用的隔离补偿）。"""
    c, shared = client
    shared._mem.clear()
    r = c.post("/api/v1/books", json={"title": "青冥录", "genre": "玄幻"})
    assert r.status_code in (200, 201), r.text
    c._book_id = r.json()["id"]  # type: ignore[attr-defined]
    yield c, shared


def test_author_create_and_list(client):
    c, shared = client
    bid = c._book_id
    r = c.post(f"/api/v1/books/{bid}/inspirations",
               json={"title": "月下剑魂", "desc": "主角在月下得到残魂指引", "type": "plot"})
    assert r.status_code == 201, r.text
    card = r.json()
    # 字段与前端 InspirationCard TS 对齐
    assert set(card.keys()) == {"id", "book_id", "icon", "title", "desc", "type", "source", "adopted", "sort_order"}
    assert card["source"] == "author"
    assert card["adopted"] is False
    assert card["book_id"] == bid

    r = c.get(f"/api/v1/books/{bid}/inspirations")
    assert r.status_code == 200
    items = r.json()
    assert any(x["id"] == card["id"] for x in items)


def test_adopt_switch(client):
    c, shared = client
    bid = c._book_id
    card_id = c.post(f"/api/v1/books/{bid}/inspirations",
                     json={"title": "旧识", "desc": "少主旧识出现", "type": "character"}).json()["id"]
    r = c.patch(f"/api/v1/inspirations/{card_id}", json={"adopted": True})
    assert r.status_code == 200
    assert r.json() == {"id": card_id, "adopted": True}
    items = c.get(f"/api/v1/books/{bid}/inspirations").json()
    target = next(x for x in items if x["id"] == card_id)
    assert target["adopted"] is True

    # 取消
    c.patch(f"/api/v1/inspirations/{card_id}", json={"adopted": False})
    target = next(x for x in c.get(f"/api/v1/books/{bid}/inspirations").json() if x["id"] == card_id)
    assert target["adopted"] is False


def test_delete_inspiration(client):
    c, shared = client
    bid = c._book_id
    card_id = c.post(f"/api/v1/books/{bid}/inspirations", json={"title": "待删"}).json()["id"]
    r = c.delete(f"/api/v1/inspirations/{card_id}")
    assert r.status_code == 204
    items = c.get(f"/api/v1/books/{bid}/inspirations").json()
    assert all(x["id"] != card_id for x in items)


def test_generate_fallback_without_llm(client):
    """FakeLLM（available=False，conftest autouse）→ generate 返回模板卡并落库。"""
    c, shared = client
    bid = c._book_id
    r = c.post(f"/api/v1/books/{bid}/inspirations/generate", json={"direction": "废材觉醒"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["book_id"] == bid
    cards = body["cards"]
    assert 1 <= len(cards) <= 5
    first = cards[0]
    assert set(first.keys()) == {"id", "book_id", "icon", "title", "desc", "type", "source", "adopted", "sort_order"}
    assert first["source"] == "chief"
    items = c.get(f"/api/v1/books/{bid}/inspirations").json()
    assert len(items) >= len(cards)


def test_plan_with_inspiration_ids_passthrough(client):
    """plan API 接受 inspiration_ids；adopted 卡会随 synopsis 传给主笔（FakeLLM 时直接 503，
    但请求应被正确解析——这里断言 503 且 detail 来自主笔不可用，而非 422 字段错误）。"""
    c, shared = client
    bid = c._book_id
    card_id = c.post(f"/api/v1/books/{bid}/inspirations", json={"title": "玄脉被夺"}).json()["id"]
    c.patch(f"/api/v1/inspirations/{card_id}", json={"adopted": True})

    # FakeLLM 不可用 → plan 会抛 ValueError → 503（证明 inspiration_ids 字段被接受，无 422）
    r = c.post(f"/api/v1/books/{bid}/plan",
               json={"direction": "废材复仇", "inspiration_ids": [card_id]})
    assert r.status_code == 503, r.text
    assert "主笔" in r.json()["detail"]

    # 未采纳的卡 id 不该进主笔上下文（仍 503，但证明字段链路 OK）
    card2 = c.post(f"/api/v1/books/{bid}/inspirations", json={"title": "未采纳卡"}).json()["id"]
    r = c.post(f"/api/v1/books/{bid}/plan",
               json={"direction": "废材复仇", "inspiration_ids": [card2]})
    assert r.status_code == 503, r.text