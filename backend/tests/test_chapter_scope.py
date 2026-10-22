"""章级副驾 + 场并入（v1.65 · 两个页面合并）测试。

动机：作者拍板「A：合并成章级工作台；章正文唯一真源，场正文只是可选项、可并入；
责编按整章干活，不用切作用域」。这批要守住四件事：

  1. 章级感知包真的把「章 / 场清单 / 前情 / 记忆 / 角色档位」装进去了（不是空壳）；
  2. 章级工具能落**章级审计**（prose_notes.chapter_id），不再"跑完就没了"；
  3. 「并入本章」幂等：并过一次再点不会重复贴；且走 save_chapter_prose（有版本兜底）；
  4. 路由/白名单/字段权限三处一致：章级工具存在、责编有权、章正文只归 editor/作者写。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402


async def _book_with_chapter_and_scenes():
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    await repo.save_book({"id": "b-ch", "title": "章级合并", "genre": "测试",
                          "status": "planned", "cover_init": "章", "synopsis": ""})
    await repo.save_chapter({"id": "c-ch-1", "book_id": "b-ch", "title": "第 1 章 雨夜",
                             "summary": "林澈在站台醒来，手里有张涂改过的路引。",
                             "order_no": 1, "tone": "suspense", "word_target": 3000,
                             "mode": "hybrid", "volume_id": "vol-b-ch-1"})
    await repo.save_chapter({"id": "c-ch-2", "book_id": "b-ch", "title": "第 2 章 街",
                             "summary": "零里街道重排。", "order_no": 2, "tone": "action",
                             "word_target": 3000})
    await repo.save_scene({"id": "s-ch-1", "chapter_id": "c-ch-1", "title": "站台",
                           "cursor_pos": 0, "stage_desc": "废弃车站，雨", "goal": "拿到路引",
                           "content_desc": "在长椅下摸到路引",
                           "final_prose": "雨水从破洞的铁皮顶浇下来。林澈摸到一张路引。"})
    await repo.save_scene({"id": "s-ch-2", "chapter_id": "c-ch-1", "title": "界墙",
                           "cursor_pos": 1, "stage_desc": "铜绿色界墙", "goal": "去第一里",
                           "content_desc": "钟声逼近",
                           "final_prose": "钟声第三次响起时，界墙亮了一线。"})
    return repo


# ---------------------------------------------------------------- 1. 章级感知包
@pytest.mark.asyncio
async def test_chapter_packet_has_chapter_scenes_prior_recall():
    from app.services.agents.chapter_perceive import build_chapter_packet, summary

    repo = await _book_with_chapter_and_scenes()
    packet = await build_chapter_packet(repo, "c-ch-1")
    kinds = {b["kind"] for b in packet["blocks"]}
    for k in ("chapter", "scenes", "constraints", "prior", "recall", "characters", "draft"):
        assert k in kinds, (k, kinds)
    assert packet["scope"] == "chapter" and packet["chapter_id"] == "c-ch-1"

    ch_block = [b for b in packet["blocks"] if b["kind"] == "chapter"][0]["items"][0]
    assert ch_block["title"].endswith("雨夜")
    assert ch_block["mode"] == "hybrid" and ch_block["word_target"] == 3000
    # 章正文（唯一真源）此时为空 → 回落到场聚合
    assert ch_block["prose_chars"] > 0

    scene_items = [b for b in packet["blocks"] if b["kind"] == "scenes"][0]["items"]
    assert [s["title"] for s in scene_items] == ["站台", "界墙"]
    assert all("prose_chars" in s for s in scene_items)

    s = summary(packet)
    assert s["scope"] == "chapter" and s["scenes"] == 2
    # 没角色卡/没前情时 dropped 要说原因（不静默）
    assert any(d["src"] in ("characters", "prior") for d in packet["dropped"])


@pytest.mark.asyncio
async def test_chapter_packet_marks_merged_scene():
    """场正文已经在章正文里 → in_chapter=True（前端「已并入」徽标靠它）。"""
    from app.services.agents.chapter_perceive import build_chapter_packet

    repo = await _book_with_chapter_and_scenes()
    await repo.save_chapter_prose("c-ch-1", "钟声第三次响起时，界墙亮了一线。")
    packet = await build_chapter_packet(repo, "c-ch-1")
    items = [b for b in packet["blocks"] if b["kind"] == "scenes"][0]["items"]
    by_title = {i["title"]: i for i in items}
    assert by_title["界墙"]["in_chapter"] is True
    assert by_title["站台"]["in_chapter"] is False


# ---------------------------------------------------------------- 2. 章级审计
@pytest.mark.asyncio
async def test_chapter_tool_writes_chapter_level_note():
    """章级工具调用要落 prose_notes（chapter_id 有值、scene_id 为空）——合并后要可追溯。"""
    from app.db.repo import Repo
    from app.services.agents.executor import call_tool
    from app.services.service import SimulationService

    repo = await _book_with_chapter_and_scenes()
    svc = SimulationService(repo)
    res = await call_tool(svc, "editor", "chapter.context", {}, chapter_id="c-ch-1")
    assert res.ok
    assert "summary" in res.data
    notes = await Repo(use_db=False).list_chapter_notes("c-ch-1")
    _ = notes  # 内存态是新实例，改用同一个 repo 查
    notes = await repo.list_chapter_notes("c-ch-1")
    assert notes, "章级工具调用没有落章级审计"
    assert notes[0]["chapter_id"] == "c-ch-1"
    assert notes[0]["scene_id"] is None


@pytest.mark.asyncio
async def test_chapter_note_is_not_visible_to_scene_list():
    """章级审计不能混进场级列表（否则场级页面会显示"不属于本场"的记录）。"""
    from app.services.service import SimulationService

    repo = await _book_with_chapter_and_scenes()
    svc = SimulationService(repo)
    await svc._save_chapter_note("c-ch-1", kind="editor", suggestion="整章节奏偏慢")
    assert await repo.list_chapter_notes("c-ch-1")
    assert await repo.list_prose_notes("s-ch-1") == []


# ---------------------------------------------------------------- 3. 并入本章
@pytest.mark.asyncio
async def test_merge_scenes_is_idempotent_and_versioned():
    from app.services.service import SimulationService

    repo = await _book_with_chapter_and_scenes()
    svc = SimulationService(repo)
    out = await svc.merge_scene_into_chapter("c-ch-1", "")
    assert out["merged"] == 2, out
    chapter = await repo.get_chapter("c-ch-1")
    body = chapter["prose_md"]
    assert "林澈摸到一张路引" in body and "界墙亮了一线" in body
    # 幂等：再并一次什么都不加（不重复贴）
    again = await svc.merge_scene_into_chapter("c-ch-1", "")
    assert again["merged"] == 0
    assert (await repo.get_chapter("c-ch-1"))["prose_md"] == body


@pytest.mark.asyncio
async def test_merge_single_scene_only():
    from app.services.service import SimulationService

    repo = await _book_with_chapter_and_scenes()
    svc = SimulationService(repo)
    out = await svc.merge_scene_into_chapter("c-ch-1", "s-ch-2")
    assert out["merged"] == 1
    body = (await repo.get_chapter("c-ch-1"))["prose_md"]
    assert "界墙亮了一线" in body and "林澈摸到一张路引" not in body


# ---------------------------------------------------------------- 4. 工具/权限/路由一致
def test_chapter_tools_registered_and_gated():
    from app.services.agents.permissions import check_write
    from app.services.agents.registry import get_tool
    from app.services.agents.spec import can_use

    for name in ("chapter.review", "chapter.verify", "chapter.polish", "chapter.scan_tone",
                 "chapter.quality_score", "chapter.inspect", "chapter.context",
                 "chapter.merge_scenes"):
        decl = get_tool(name)
        assert decl is not None, name
        assert "chapter_id" in decl.params, name
        assert can_use("editor", name), name
        assert not can_use("chief", name), name      # 主笔不碰正文审校
    assert get_tool("chapter.merge_scenes").needs_confirm is True
    # 字段级：章正文只有 editor / 作者能写
    assert check_write("editor", "chapters:prose") is None
    assert check_write("chief", "chapters:prose") is not None


def test_chapter_agent_routes_exist():
    from app.main import app

    # 注意：这版 FastAPI 用 _IncludedRouter 懒注册，app.routes 里拿不到扁平路径 ——
    # 走 openapi 视图（与前端实际能调到的路径一致）。
    paths = set(app.openapi().get("paths", {}).keys())
    for p in ("/api/v1/chapters/{chapter_id}/agent/chat",
              "/api/v1/chapters/{chapter_id}/agent/chat/stream",
              "/api/v1/chapters/{chapter_id}/agent/run/stream",
              "/api/v1/chapters/{chapter_id}/agent/notes",
              "/api/v1/chapters/{chapter_id}/merge-scenes"):
        assert p in paths, p


# ---------------------------------------------------------------- 5. 收口守卫（v1.66）
def test_chapter_tools_never_require_scene_id():
    '''章级工具一律以 chapter_id 为单位：不许再要 scene_id（否则合并又变成「必须拆场」）。'''
    from app.services.agents.registry import TOOLS

    for name, decl in TOOLS.items():
        if decl.group != "chapter":      # chapter.production 是主笔的结构工具，不属章级审校
            continue
        assert "chapter_id" in decl.params, name
        assert "scene_id" not in decl.params, name
        assert decl.group == "chapter", name


def test_chapter_tools_do_not_write_scene_prose():
    '''章级工具不得写 scenes:final_prose（那是场级页的事）——章级只碰章正文与审计。'''
    from app.services.agents.registry import TOOLS

    for name, decl in TOOLS.items():
        if decl.group != "chapter":
            continue
        assert decl.writes != "scenes:final_prose", name


@pytest.mark.asyncio
async def test_merge_keeps_previous_prose_as_revision():
    '''并入 = 改写章正文 → 必须先存旧版（v1.61 稿本），否则并错了无从回退。'''
    from app.services.service import SimulationService

    repo = await _book_with_chapter_and_scenes()
    await repo.save_chapter_prose("c-ch-1", "作者手写的一段开头。")
    svc = SimulationService(repo)
    await svc.merge_scene_into_chapter("c-ch-1", "")
    body = (await repo.get_chapter("c-ch-1"))["prose_md"]
    assert body.startswith("作者手写的一段开头。")
    versions = await repo.list_chapter_revisions("c-ch-1")
    assert versions, "并入覆盖了章正文却没留版本"

