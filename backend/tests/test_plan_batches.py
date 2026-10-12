"""A4 分批交付（主笔每批 N 章）+ B33 骨架落库防误删。

覆盖：
- plan_skelly 分批：prompt 带本批章号区间与「必须承接」的已落库章节；模型多排 → 按批次契约截断（不静默）；
- plan_book 分批：batch_from 默认 = 已落库章数 + 1，locked 传已落库章节；
- commit 追加（默认 mode=append）：不动已有章节与正文，章号往后接；
- commit 替换（mode=replace）：书里已有正文却未 allow_overwrite → 409，正文必须完好；
- allow_overwrite=True → 真的替换（把破坏性语义写死在测试里，防止以后被"优化"掉）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
# 注：async 用例逐个打 @pytest.mark.asyncio；TestClient 用例是同步的，不加模块级 marker


# ----------------------------------------------------------------------
# 引擎层：分批 prompt / 截断
# ----------------------------------------------------------------------
class _FakeLLM:
    def __init__(self, out):
        self.available = True
        self._out = out
        self.calls: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self._out

    async def call_strong(self, prompt, json_schema=None):
        return await self.call_cheap(prompt, json_schema)


@pytest.mark.asyncio
async def test_batch_prompt_carries_range_and_locked(monkeypatch):
    """分批模式：prompt 必须写清本批区间，并把已落库章节作为「必须承接」喂进去。"""
    import app.services.engine.chief_planner as cp

    llm = _FakeLLM({"worldview": {}, "chapters": [
        {"seq": 2, "title": "第二章", "scenes": []},
        {"seq": 3, "title": "第三章", "scenes": []},
    ], "foreshadow_plan": [], "ending_options": []})
    monkeypatch.setattr(cp, "llm_client", llm)

    raw = await cp.plan_skelly("一句话方向", "书信息", batch_from=2, batch_size=2,
                               locked=[{"order_no": 1, "title": "第一章", "summary": "主角醒来"}])
    prompt = llm.calls[0]
    assert "本批只排第 2 章 ~ 第 3 章" in prompt
    assert "第1章《第一章》：主角醒来" in prompt
    assert raw["batch"]["from"] == 2 and raw["batch"]["to"] == 3
    assert raw["batch"]["asked"] == 2 and raw["batch"]["got"] == 2 and raw["batch"]["trimmed"] == 0


@pytest.mark.asyncio
async def test_batch_truncates_overproduction(monkeypatch):
    """模型多排时按批次契约截断，并把 trimmed 如实回报（不静默吞掉）。"""
    import app.services.engine.chief_planner as cp

    llm = _FakeLLM({"worldview": {}, "chapters": [
        {"title": f"第{i}章", "scenes": []} for i in range(1, 8)
    ], "foreshadow_plan": [], "ending_options": []})
    monkeypatch.setattr(cp, "llm_client", llm)

    raw = await cp.plan_skelly("方向", "ctx", batch_from=1, batch_size=3)
    assert len(raw["chapters"]) == 3
    assert raw["batch"] == {"from": 1, "to": 3, "asked": 3, "got": 3, "trimmed": 4}
    assert "（无，本批是第一批）" in llm.calls[0]


@pytest.mark.asyncio
async def test_no_batch_mode_keeps_old_behaviour(monkeypatch):
    """不传 batch_size → 老行为（一次排完），prompt 不含分批段、结果无 batch 键。"""
    import app.services.engine.chief_planner as cp

    llm = _FakeLLM({"worldview": {"premise": "x"}, "chapters": [
        {"title": "第一章", "scenes": []}], "foreshadow_plan": [], "ending_options": []})
    monkeypatch.setattr(cp, "llm_client", llm)

    raw = await cp.plan_skelly("方向", "ctx")
    assert "分批交付" not in llm.calls[0]
    assert "batch" not in raw


# ----------------------------------------------------------------------
# API 层：追加 / 替换 / 防误删
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from app.db.repo import Repo
    from app.api.deps import get_repo
    from app.main import app

    shared = Repo(use_db=False)

    async def _fake_repo():
        return shared

    app.dependency_overrides[get_repo] = _fake_repo
    with TestClient(app) as c:
        c._shared_repo = shared  # type: ignore[attr-defined]
        yield c, shared
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh(client):
    """每用例重置内存态并重建测试书（module 级 client 复用的隔离补偿）。"""
    c, shared = client
    shared._mem.clear()
    r = c.post("/api/v1/books", json={"title": "分批录", "genre": "玄幻"})
    assert r.status_code in (200, 201), r.text
    c._book_id = r.json()["id"]  # type: ignore[attr-defined]
    yield c, shared


PROSE = "雨点敲在窗上，陈默把残页按进袖口，指节泛白。"


def _seed_written_chapter(c, bid) -> tuple[str, str]:
    """建 1 章 1 场并写定稿正文（充当"已有稿子"）。"""
    ch = c.post(f"/api/v1/books/{bid}/chapters", json={"title": "第一章 旧稿"})
    assert ch.status_code == 201, ch.text
    cid = ch.json()["id"]
    sc = c.post(f"/api/v1/chapters/{cid}/scenes", json={"title": "旧场景"})
    assert sc.status_code == 201, sc.text
    sid = sc.json()["id"]
    p = c.put(f"/api/v1/scenes/{sid}/prose", json={"text": PROSE})
    assert p.status_code == 200, p.text
    return cid, sid


def _titles(c, bid) -> list[str]:
    tree = c.get(f"/api/v1/books/{bid}/tree").json()
    return [ch["title"] for ch in tree.get("chapters", [])]


def test_append_commit_keeps_existing_chapter_and_prose(client):
    """默认追加：已有章节与正文原样保留，新章从书末往后接。"""
    c, _ = client
    bid = c._book_id
    cid, sid = _seed_written_chapter(c, bid)

    plan = {"chapters": [
        {"title": "第二章 新批次", "summary": "承接旧稿", "scenes": [{"title": "新场景"}]},
        {"title": "第三章", "summary": "", "scenes": []},
    ]}
    r = c.post(f"/api/v1/books/{bid}/plan/commit", json={"plan": plan, "mode": "append"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "append" and body["chapters"] == 2
    assert body["total_chapters"] == 3 and body["replaced_chapters"] == 0
    assert body["kept_prose_scenes"] == 1

    assert _titles(c, bid) == ["第一章 旧稿", "第二章 新批次", "第三章"]
    # 旧章正文一字未动
    assert c.get(f"/api/v1/scenes/{sid}/prose").json()["prose"] == PROSE
    assert c.get(f"/api/v1/scenes/{sid}").status_code == 200


def test_replace_without_confirm_is_409_and_keeps_prose(client):
    """B33：替换会级联删稿 → 未显式 allow_overwrite 时必须 409，且稿子完好。"""
    c, _ = client
    bid = c._book_id
    _, sid = _seed_written_chapter(c, bid)

    plan = {"chapters": [{"title": "重排第一章", "summary": "", "scenes": []}]}
    r = c.post(f"/api/v1/books/{bid}/plan/commit", json={"plan": plan, "mode": "replace"})
    assert r.status_code == 409, r.text
    assert "正文" in r.json()["detail"]
    assert _titles(c, bid) == ["第一章 旧稿"]
    assert c.get(f"/api/v1/scenes/{sid}/prose").json()["prose"] == PROSE


def test_replace_with_confirm_actually_replaces(client):
    """显式确认 → 真替换（章数被替换、旧场景正文随场景删除）。"""
    c, _ = client
    bid = c._book_id
    _, sid = _seed_written_chapter(c, bid)

    plan = {"chapters": [{"title": "重排第一章", "summary": "", "scenes": [{"title": "A"}]}]}
    r = c.post(f"/api/v1/books/{bid}/plan/commit",
               json={"plan": plan, "mode": "replace", "allow_overwrite": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "replace" and body["replaced_chapters"] == 1
    assert _titles(c, bid) == ["重排第一章"]
    # 旧场景随章级联删除（DB 由 ORM cascade，内存态在 repo.delete_scene/delete_chapter 里补齐）
    assert c.get(f"/api/v1/scenes/{sid}/prose").status_code == 404


def test_append_on_empty_book_writes_worldview_once(client):
    """空书首批：写世界观 + 章节；第二批不再重写世界观（追加语义）。"""
    import asyncio

    c, shared = client
    bid = c._book_id
    first = {"worldview": {"premise": "旧上海，纸媒将亡"},
             "chapters": [{"title": "第一章", "scenes": []}]}
    r = c.post(f"/api/v1/books/{bid}/plan/commit", json={"plan": first, "mode": "append"})
    assert r.status_code == 200, r.text
    assert r.json()["first_batch"] is True

    second = {"chapters": [{"title": "第二章", "scenes": []}]}
    r = c.post(f"/api/v1/books/{bid}/plan/commit", json={"plan": second, "mode": "append"})
    assert r.status_code == 200, r.text
    assert r.json()["first_batch"] is False
    assert _titles(c, bid) == ["第一章", "第二章"]
    book = asyncio.run(shared.get_book(bid))
    assert "旧上海" in (book or {}).get("worldview_json", "")


def test_commit_empty_plan_is_rejected(client):
    """空骨架不静默成功：直接 400，避免"以为落库了其实没写"。"""
    c, _ = client
    r = c.post(f"/api/v1/books/{c._book_id}/plan/commit", json={"plan": {}, "mode": "append"})
    assert r.status_code == 400, r.text
