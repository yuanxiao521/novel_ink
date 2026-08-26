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


# ---------------------------------------------------------------- P0 正文落库（v1.2）

def _make_book_chapter_scene(client, title="落库书", scene_titles=("雨夜",)):
    """建书→章→场景（内存态 API），返回 (book_id, chapter_id, [scene_id])。"""
    r = client.post("/api/v1/books", json={"title": title, "genre": "玄幻", "synopsis": "一场关于信念的推演"})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    r = client.post(f"/api/v1/books/{bid}/chapters", json={"title": "第一章", "order_no": 1})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    sids = []
    for j, st in enumerate(scene_titles):
        r = client.post(f"/api/v1/chapters/{cid}/scenes", json={"title": st, "cursor_pos": j + 1})
        assert r.status_code == 201, r.text
        sids.append(r.json()["id"])
    return bid, cid, sids


def _run_and_finalize(client, bid, cid, sid):
    """启动 sim → 推进 1 回合（FakeLLM 产出成文）→ 手动定稿，返回定稿响应。"""
    r = client.post("/api/v1/sims", json={"book_id": bid, "chapter_id": cid, "scene_id": sid, "resume": False})
    assert r.status_code == 201, r.text
    sim_id = r.json()["sim_id"]
    r = client.post(f"/api/v1/sims/{sim_id}/step", params={"n": 1})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/sims/{sim_id}/finalize")
    assert r.status_code == 200, r.text
    return r.json()


def test_finalize_scene_prose(client):
    """P0 正文落库：step 产出成文 → 手动定稿 → 场景正文可查 + 幂等覆盖。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "落库书")

    body = _run_and_finalize(client, bid, cid, sid)
    assert body["scene_id"] == sid
    assert body["chapter_id"] == cid
    assert body["book_id"] == bid
    assert body["word_count"] > 0
    assert body["prose"]

    # 场景正文端点可查
    r = client.get(f"/api/v1/scenes/{sid}/prose")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["finalized"] is True
    assert p["word_count"] == body["word_count"]
    assert p["prose"] == body["prose"]

    # 幂等：再次定稿不报错，内容一致
    r = client.post("/api/v1/sims/" + _last_sim_id(client, sid) + "/finalize")
    assert r.status_code == 200, r.text
    assert r.json()["word_count"] == body["word_count"]


def _last_sim_id(client, scene_id):
    """取该场景最新 sim（内存态下通过新建一个 resume 查询不适用，直接重新跑一个）。"""
    r = client.post("/api/v1/sims", json={"scene_id": scene_id, "resume": True})
    assert r.status_code == 201, r.text
    return r.json()["sim_id"]


def test_chapter_prose_aggregate(client):
    """P0 章节聚合：一章两场景各自定稿 → 章正文按 cursor_pos 顺序拼接。"""
    bid, cid, (s1, s2) = _make_book_chapter_scene(client, "聚合书", scene_titles=("雨夜", "拂晓"))

    f1 = _run_and_finalize(client, bid, cid, s1)
    f2 = _run_and_finalize(client, bid, cid, s2)

    r = client.get(f"/api/v1/chapters/{cid}/prose")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["chapter_id"] == cid
    assert [s["scene_id"] for s in p["scenes"]] == [s1, s2]  # 按 cursor_pos 排序
    assert all(s["finalized"] for s in p["scenes"])
    # 聚合正文 = 场景1 + \n\n + 场景2
    assert p["prose"] == f1["prose"] + "\n\n" + f2["prose"]
    assert p["word_count"] == len(p["prose"])


def test_book_export_markdown(client):
    """P0 全书导出：md 含书名/简介/章标题/场景正文，attachment 下载头。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "导出书")
    _run_and_finalize(client, bid, cid, sid)

    r = client.get(f"/api/v1/books/{bid}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "# 导出书" in r.text
    assert "> 一场关于信念的推演" in r.text
    assert "## 第一章" in r.text

    # 无定稿内容的书导出 → 400
    r = client.post("/api/v1/books", json={"title": "空书", "genre": "玄幻"})
    empty_bid = r.json()["id"]
    r = client.get(f"/api/v1/books/{empty_bid}/export")
    assert r.status_code == 400