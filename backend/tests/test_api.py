"""API 集成冒烟（内存态 + TestClient）：验证端点连通，无 DB 也可测。

依赖覆盖：把 deps.get_repo 换成 use_db=False 的内存态 Repo（不连真实 PG）。
TestClient 直接驱动 async 路由（Starlette 内部 run_sync 处理）。
"""
import json
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

    # 模块级共享内存态 Repo：跨请求共享 _mem，start 后 step/stream 能取到同一 sim
    # module scope：TestClient 只建一次（lifespan 只跑一次迁移），大幅缩短真库交互耗时
    shared = Repo(use_db=False)

    async def _fake_repo():
        return shared

    app.dependency_overrides[get_repo] = _fake_repo

    with TestClient(app) as c:
        c._shared_repo = shared  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_mem(client):
    """每用例重置共享内存态，保证用例间状态隔离（module 级 client 复用的补偿）。"""
    repo = client._shared_repo  # type: ignore[attr-defined]
    repo._mem.clear()
    yield


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
    """建书/章/场景 + 书级角色 → start sim → step 1（空黑板下不再注入静态角色）。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "启停书")
    r = client.post(
        "/api/v1/sims",
        json={"book_id": bid, "chapter_id": cid, "scene_id": sid, "resume": False},
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
    bid, cid, (sid,) = _make_book_chapter_scene(client, "流式书")
    r = client.post(
        "/api/v1/sims",
        json={"book_id": bid, "chapter_id": cid, "scene_id": sid, "resume": False},
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

def _char_spec(cid: str, name: str) -> str:
    """书级角色卡 ④层 spec_json（CharacterCard 全字段，带一条动态目标供调权/曝光测试）。"""
    return json.dumps({
        "id": cid, "name": name, "summary": f"{name}的人设", "traits": [],
        "voice": "", "core_beliefs": [],
        "dynamic_goals": [{"id": "g-1", "text": "查明真相", "weight": 0.5, "last_adjust_reason": ""}],
        "bottom_lines": [],
        "system_prompt": "", "think_schema": "", "decide_schema": "", "static_world": "",
    }, ensure_ascii=False)


def _make_book_chapter_scene(client, title="落库书", scene_titles=("雨夜",)):
    """建书→章→场景（内存态 API），返回 (book_id, chapter_id, [scene_id])。

    v1.6 空黑板装配不再注入静态角色 → 这里显式补 3 张书级角色卡，
    保证 start 后的 sim 有上场角色（测试不依赖旧兜底）。
    """
    r = client.post("/api/v1/books", json={"title": title, "genre": "玄幻", "synopsis": "一场关于信念的推演"})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    r = client.post(f"/api/v1/books/{bid}/chapters", json={"title": "第一章", "order_no": 1})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    sids = []
    for j, st in enumerate(scene_titles):
        r = client.post(f"/api/v1/chapters/{cid}/scenes", json={
            "title": st, "cursor_pos": j + 1,
            # 空黑板装配不再回退剧情 facts → 测试显式携带一条初始事实供 intervene/成文
            "initial_facts_json": json.dumps([
                {"id": f"F-{j + 1}", "text": f"场景初始事实{j + 1}", "kind": "env", "visible_to": [], "active": True},
            ]),
        })
        assert r.status_code == 201, r.text
        sids.append(r.json()["id"])
    for cid_, nm in (("chenmo", "陈默"), ("liwen", "李文"), ("zhoushen", "周婶")):
        r = client.post(f"/api/v1/books/{bid}/characters",
                        json={"name": nm, "spec_json": _char_spec(cid_, nm)})
        assert r.status_code == 201, r.text
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


def _start_sim(client, bid, cid, sid):
    r = client.post("/api/v1/sims", json={"book_id": bid, "chapter_id": cid, "scene_id": sid, "resume": False})
    assert r.status_code == 201, r.text
    return r.json()["sim_id"]


def test_inject_palette_lists_facts_and_goals(client):
    """介入工具数据源：start sim 后 palette 返回运行时 facts（可曝光）+ 角色动态目标（可调权）。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "介入书")
    sim_id = _start_sim(client, bid, cid, sid)

    r = client.get(f"/api/v1/sims/{sim_id}/inject-palette")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["facts"], "运行时 facts 非空（场景无初始事实时回退规范 facts）"
    assert all("id" in f and "text" in f for f in p["facts"])
    assert p["characters"]
    assert all("id" in c and "name" in c and "dynamic_goals" in c for c in p["characters"])
    assert any(c["dynamic_goals"] for c in p["characters"]), "betrayal_night 卡应带动态目标"


def test_intervene_expose_writes_belief(client):
    """信息曝光：把某 fact 记为目标角色 belief（来源 DIR）；非法 fact/角色 → 400。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "曝光书")
    sim_id = _start_sim(client, bid, cid, sid)

    p = client.get(f"/api/v1/sims/{sim_id}/inject-palette").json()
    fact = p["facts"][0]
    target = p["characters"][0]["id"]

    r = client.post(
        f"/api/v1/sims/{sim_id}/intervene",
        json={"action": "expose", "payload": {"fact_id": fact["id"], "target_char_id": target, "channel": "told"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    st = client.get(f"/api/v1/sims/{sim_id}/state").json()
    beliefs = st.get("beliefs", {}).get(target, [])
    assert any(b["fact_id"] == fact["id"] for b in beliefs), "目标角色应已记录该 belief"

    # 非法 fact → 400
    r = client.post(
        f"/api/v1/sims/{sim_id}/intervene",
        json={"action": "expose", "payload": {"fact_id": "NOPE", "target_char_id": target}},
    )
    assert r.status_code == 400


def test_intervene_adjust_weight_requires_reason(client):
    """目标权重调整：缺剧情内因 400（§6.1 因果律）；带原因则权重变化。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "调权书")
    sim_id = _start_sim(client, bid, cid, sid)

    p = client.get(f"/api/v1/sims/{sim_id}/inject-palette").json()
    c0 = next(c for c in p["characters"] if c["dynamic_goals"])
    goal = c0["dynamic_goals"][0]
    before = goal["weight"]

    # 无内因 → 400
    r = client.post(
        f"/api/v1/sims/{sim_id}/intervene",
        json={"action": "adjust_weight", "payload": {"char_id": c0["id"], "goal_id": goal["id"], "delta": 0.2}},
    )
    assert r.status_code == 400

    # 带剧情内因 → 200，权重 +0.2（clamp 后仍准）
    r = client.post(
        f"/api/v1/sims/{sim_id}/intervene",
        json={"action": "adjust_weight",
              "payload": {"char_id": c0["id"], "goal_id": goal["id"], "delta": 0.2,
                          "reason": "李文深夜造访书房，真相逼近"}},
    )
    assert r.status_code == 200, r.text

    p2 = client.get(f"/api/v1/sims/{sim_id}/inject-palette").json()
    g2 = next(g for c in p2["characters"] if c["id"] == c0["id"] for g in c["dynamic_goals"] if g["id"] == goal["id"])
    assert abs(g2["weight"] - (before + 0.2)) < 1e-6


def test_book_character_crud_book_level(client):
    """书级角色库（v1.4）：POST/GET /books/{id}/characters → PUT/DELETE /characters/{id}。

    书级角色 scene_id 为空、book_id 指向书；不消费 token 的纯 CRUD 冒烟。
    """
    bid = client.post(
        "/api/v1/books", json={"title": "角色库测试书", "genre": "玄幻", "status": "planned"},
    ).json()["id"]

    # 书级新建
    r = client.post(
        f"/api/v1/books/{bid}/characters",
        json={"name": "新侠", "spec_json": json.dumps({
            "id": "", "name": "新侠", "summary": "测试角色", "traits": [],
            "voice": "", "core_beliefs": [], "dynamic_goals": [],
            "bottom_lines": [], "system_prompt": "", "think_schema": "",
            "decide_schema": "", "static_world": "",
        })},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # 书级列表：1 条，scene_id 为空（全书共享）
    lst = client.get(f"/api/v1/books/{bid}/characters").json()
    assert len(lst) == 1 and lst[0]["id"] == cid
    assert lst[0]["scene_id"] is None
    assert lst[0]["book_id"] == bid

    # 部分更新不丢其他字段
    r = client.put(f"/api/v1/characters/{cid}", json={"name": "新侠·改"})
    assert r.status_code == 200, r.text
    lst2 = client.get(f"/api/v1/books/{bid}/characters").json()
    assert len(lst2) == 1 and lst2[0]["name"] == "新侠·改"
    assert "spec_json" in lst2[0] and lst2[0]["spec_json"]

    # 删除
    r = client.delete(f"/api/v1/characters/{cid}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/books/{bid}/characters").json() == []


# ---------------------------------------------------------------- 信念账本 + Dashboard（v1.5）

def test_belief_crud_book_level(client):
    """信念账本 CRUD（v1.5）：POST/GET（含 char_id/channel 过滤）→ PUT → DELETE；非法 channel 400。"""
    bid = client.post(
        "/api/v1/books", json={"title": "信念书", "genre": "悬疑"},
    ).json()["id"]

    # 手写信念（edited=True）
    r = client.post(
        f"/api/v1/books/{bid}/beliefs",
        json={"char_id": "chenmo", "channel": "perceived", "text": "保险柜的门半开着", "confidence": 0.9},
    )
    assert r.status_code == 201, r.text
    b1 = r.json()["id"]
    r = client.post(
        f"/api/v1/books/{bid}/beliefs",
        json={"char_id": "chenmo", "channel": "inferred", "text": "李文知道里面是什么"},
    )
    assert r.status_code == 201, r.text
    b2 = r.json()["id"]
    # 另一角色，验证 char_id 过滤
    r = client.post(
        f"/api/v1/books/{bid}/beliefs",
        json={"char_id": "liwen", "channel": "told", "text": "李文被告知手稿存在"},
    )
    assert r.status_code == 201, r.text

    # 全量 + edited 标记
    lst = client.get(f"/api/v1/books/{bid}/beliefs").json()
    assert len(lst) == 3 and all(b["edited"] for b in lst)
    assert all(b["book_id"] == bid for b in lst)

    # char_id 过滤
    lst_cm = client.get(f"/api/v1/books/{bid}/beliefs", params={"char_id": "chenmo"}).json()
    assert len(lst_cm) == 2

    # channel 过滤
    lst_ch = client.get(f"/api/v1/books/{bid}/beliefs", params={"channel": "told"}).json()
    assert len(lst_ch) == 1 and lst_ch[0]["char_id"] == "liwen"

    # 更新：改置信度 → edited 仍 True
    r = client.put(f"/api/v1/beliefs/{b1}", json={"confidence": 0.6, "text": "保险柜的门虚掩着"})
    assert r.status_code == 200, r.text
    lst2 = client.get(f"/api/v1/books/{bid}/beliefs", params={"char_id": "chenmo"}).json()
    upd = next(b for b in lst2 if b["id"] == b1)
    assert abs(upd["confidence"] - 0.6) < 1e-6
    assert upd["text"] == "保险柜的门虚掩着"

    # 非法 channel 400
    r = client.post(
        f"/api/v1/books/{bid}/beliefs",
        json={"char_id": "chenmo", "channel": "telepathy", "text": "x"},
    )
    assert r.status_code == 400

    # 删除
    assert client.delete(f"/api/v1/beliefs/{b2}").status_code == 204
    lst3 = client.get(f"/api/v1/books/{bid}/beliefs").json()
    assert len(lst3) == 2 and all(b["id"] != b2 for b in lst3)


def test_intervene_expose_syncs_beliefs(client):
    """贯通（v1.5）：导演 expose 事实 → sim 信念账本自动同步到书级 Belief 表（edited=False）。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "信念同步书")
    r = client.post("/api/v1/sims", json={"book_id": bid, "chapter_id": cid, "scene_id": sid, "resume": False})
    assert r.status_code == 201, r.text
    sim_id = r.json()["sim_id"]

    p = client.get(f"/api/v1/sims/{sim_id}/inject-palette").json()
    fact = p["facts"][0]
    target = p["characters"][0]["id"]

    r = client.post(
        f"/api/v1/sims/{sim_id}/intervene",
        json={"action": "expose", "payload": {"fact_id": fact["id"], "target_char_id": target, "channel": "told"}},
    )
    assert r.status_code == 200, r.text

    bl = client.get(f"/api/v1/books/{bid}/beliefs", params={"char_id": target}).json()
    assert any(b["fact_id"] == fact["id"] and b["edited"] is False and b["channel"] == "told" for b in bl)


def test_dashboard_aggregate(client):
    """Dashboard 聚合（v1.5）：空书契约字段齐全；定稿场景后 kpi/时间线状态联动。"""
    bid, cid, (s1, s2) = _make_book_chapter_scene(client, "概览书", scene_titles=("雨", "晴"))

    d = client.get(f"/api/v1/books/{bid}/dashboard").json()
    assert d["book"]["id"] == bid
    assert d["kpi"]["chapters_total"] == 1 and d["kpi"]["chapters_done"] == 0
    assert len(d["timeline"]) == 1
    assert d["timeline"][0]["status"] in {"draft", "current", "planned"}
    assert len(d["heat"]) == 28
    assert isinstance(d["todos"], list) and isinstance(d["quotes"], list)
    assert 0 <= d["kpi"]["health"] <= 100

    # 定稿全部场景 → 章 done + 字数 > 0
    _run_and_finalize(client, bid, cid, s1)
    _run_and_finalize(client, bid, cid, s2)
    d2 = client.get(f"/api/v1/books/{bid}/dashboard").json()
    assert d2["kpi"]["chapters_done"] == 1
    assert d2["kpi"]["word_count"] > 0
    assert d2["timeline"][0]["status"] == "done"


# ---------------------------------------------------------------- 空黑板 + 选角（v1.6）

def test_empty_world_no_fallback(client):
    """空黑板（v1.6）：新书空场景（无角色无事实）start → 不再注入静态剧情/角色。"""
    bid = client.post("/api/v1/books", json={"title": "空黑板书", "genre": "玄幻"}).json()["id"]
    cid = client.post(f"/api/v1/books/{bid}/chapters", json={"title": "第一章", "order_no": 1}).json()["id"]
    sid = client.post(f"/api/v1/chapters/{cid}/scenes", json={"title": "空场", "cursor_pos": 1}).json()["id"]

    r = client.post("/api/v1/sims", json={"book_id": bid, "chapter_id": cid, "scene_id": sid, "resume": False})
    assert r.status_code == 201, r.text
    sim_id = r.json()["sim_id"]

    body = client.get(f"/api/v1/sims/{sim_id}/state").json()
    assert body["characters"] == []          # 无静态角色注入
    assert body["world"]["facts"] == []       # 无剧情事实注入
    assert body["empty_world"] is True        # 空世界标记（前端引导选角）


def test_cast_replaces_roles(client):
    """选角（v1.6）：start 3 角色 → PUT cast 仅留 1 → characters 更新、信念裁剪、facts/回合保留。"""
    bid, cid, (sid,) = _make_book_chapter_scene(client, "选角书")
    r = client.post("/api/v1/sims", json={"book_id": bid, "chapter_id": cid, "scene_id": sid, "resume": False})
    sim_id = r.json()["sim_id"]

    st0 = client.get(f"/api/v1/sims/{sim_id}/state").json()
    assert len(st0["characters"]) == 3
    keep, drop = st0["characters"][0], st0["characters"][1]

    # 先给 drop 角色曝一条信念（cast 后应被裁剪）
    p = client.get(f"/api/v1/sims/{sim_id}/inject-palette").json()
    fact = p["facts"][0]
    r = client.post(
        f"/api/v1/sims/{sim_id}/intervene",
        json={"action": "expose", "payload": {"fact_id": fact["id"], "target_char_id": drop, "channel": "told"}},
    )
    assert r.status_code == 200, r.text

    facts_before = len(client.get(f"/api/v1/sims/{sim_id}/state").json()["world"]["facts"])
    assert facts_before >= 1

    # cast 仅保留 keep
    r = client.put(f"/api/v1/sims/{sim_id}/cast", json={"character_ids": [keep]})
    assert r.status_code == 200, r.text
    assert r.json()["characters"] == [keep]

    st = client.get(f"/api/v1/sims/{sim_id}/state").json()
    assert st["characters"] == [keep]
    assert drop not in st["beliefs"]        # 非上场者 belief 清空
    assert len(st["world"]["facts"]) == facts_before  # facts 保留（cast 不重启局面）

    # 非法角色 → 400
    r = client.put(f"/api/v1/sims/{sim_id}/cast", json={"character_ids": ["ghost"]})
    assert r.status_code == 400


def test_update_inspiration(client):
    """灵感编辑（v1.6）：PUT 改 title/desc → GET 反映；不碰 adopted。"""
    bid = client.post("/api/v1/books", json={"title": "灵感编辑书", "genre": "玄幻"}).json()["id"]
    r = client.post(f"/api/v1/books/{bid}/inspirations",
                    json={"title": "旧标题", "desc": "旧描述", "type": "plot"})
    assert r.status_code == 201, r.text
    card_id = r.json()["id"]

    r = client.put(f"/api/v1/inspirations/{card_id}", json={"title": "新标题", "desc": "新描述"})
    assert r.status_code == 200, r.text

    cards = client.get(f"/api/v1/books/{bid}/inspirations").json()
    upd = next(c for c in cards if c["id"] == card_id)
    assert upd["title"] == "新标题"
    assert upd["desc"] == "新描述"
    assert upd["adopted"] is False