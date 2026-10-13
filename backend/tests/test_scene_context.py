"""写手素材筛选与 prompt 装配（v1.44 · B48/B49/B50）——纯函数 + 一次集成校验。

为什么单独一个文件：这三条都是「素材选错了 / 选重了 / 选脏了」的问题——不报错、不崩，
只表现为模型输出质量下降（实测体检抓到「本场设定栏全空；主角指代与猎/被猎立场倒置，
结尾跳步」）。只能靠断言看住，否则下次改动又会悄悄退化。
"""
from __future__ import annotations

import pytest
import pytest_asyncio

# 只给 async 用例打标（本文件多数是纯函数用例，全局打标会招来 9 条 PytestWarning）
from app.db.repo import Repo
from app.services.engine.prose import _scene_block, draft_prompt
from app.services.engine.scene_context import drop_note, is_dirty_name, select_world_states
from app.services.service import SimulationService


def ws(kind, name, value, edited=False):
    return {"kind": kind, "name": name, "value": value, "edited": edited}


# ---------------------------------------------------------------- 纯函数：筛选规则
def test_item_term_selected_by_scene_relevance():
    """B48：item/term 按「本场设定/正文里出现过」注入，而不是拿物品名去比角色名集合。

    修前：kind in {character,item,term} and name in cast_names —— 物品名永远不是角色名，
    所以这两类**永远注入不进来**（实测正文两次提到「异常者」、一次提到「金属徽章」，全被裁）。
    """
    states = [ws("item", "金属徽章", "携带"), ws("term", "异常者", "异常者"),
              ws("item", "无关道具", "放在别处")]
    kept, dropped = select_world_states(
        states, {"沈拂"}, scene_text="雨夜追猎",
        prose_text="夹克内袋那枚金属徽章震了一下，他靠近异常者才会这样。")
    assert {k["name"] for k in kept} == {"金属徽章", "异常者"}
    assert [d["name"] for d in dropped] == ["无关道具"]
    assert "未出现" in dropped[0]["reason"]


def test_character_filtered_by_cast():
    states = [ws("character", "沈拂", "离开现场"), ws("character", "伞下人", "在屋顶")]
    kept, dropped = select_world_states(states, {"沈拂"}, scene_text="", prose_text="")
    assert [k["name"] for k in kept] == ["沈拂"]
    assert dropped[0]["reason"] == "该角色本场未上场"


def test_pronoun_dirty_name_dropped():
    """B50：代词脏行（name=「他」）进不来，且**给出原因**（不静默）。"""
    kept, dropped = select_world_states(
        [ws("character", "他", "离开顶层（推门后未进屋）")], {"沈拂"},
        scene_text="", prose_text="")
    assert kept == []
    assert "代词" in dropped[0]["reason"]
    assert is_dirty_name("他") and is_dirty_name("") and not is_dirty_name("沈拂")


def test_author_edited_always_kept():
    """B27b 例外：作者手改行即使名字是代词也必须注入（作者权威 > 过滤规则）。"""
    kept, _ = select_world_states(
        [ws("numeric", "他:与撑黑伞人影距离", "10米", edited=True)], set(),
        scene_text="", prose_text="")
    assert len(kept) == 1


def test_numeric_time_kept_and_empty_value_dropped():
    states = [ws("numeric", "战力", "1200"), ws("time", "当前时间", "夜间"),
              ws("item", "空道具", "")]
    kept, dropped = select_world_states(states, set(), scene_text="", prose_text="")
    assert {k["name"] for k in kept} == {"战力", "当前时间"}
    assert dropped[0]["reason"] == "值为空"


def test_drop_note_is_readable():
    note = drop_note([{"name": "他", "reason": "名字是代词/占位（记账未落到具体对象）"}])
    assert "他" in note and "代词" in note


# ---------------------------------------------------------------- prompt 装配
def test_scene_block_marks_missing_fields():
    """v1.44：设定缺失必须**显式标注**（原实现渲染成空标签 → 模型不知道"没设定"，自由发挥）。"""
    out = _scene_block({"title": "场景 1"})
    assert "标题：场景 1" in out
    assert out.count("（未填") == 3
    assert "不要自行编造" in out


def test_draft_prompt_has_gap_discipline():
    p = draft_prompt({"title": "场景 1"}, "", "", characters=[], world_states=[])
    assert "设定缺口纪律" in p
    assert "不要编造" in p
    assert "（新写开场）" in p


def test_draft_prompt_parses_character_spec_json():
    """角色卡数据在 spec_json 列里——prompt 必须解析它（B16 的字段名对齐不能回退）。"""
    card = {"id": "c1", "name": "沈拂",
            "spec_json": '{"summary": "前档案管理员", "voice": "短句，省略主语", "bottom_lines": ["不赌命"]}'}
    p = draft_prompt({"title": "x"}, "", "", characters=[card])
    assert "沈拂" in p and "短句，省略主语" in p and "不赌命" in p


# ---------------------------------------------------------------- 集成：真实装配路径
@pytest_asyncio.fixture
async def svc():
    repo = Repo(use_db=False)
    service = SimulationService(repo)
    await repo.save_book({"id": "book-c", "title": "素材书", "genre": "悬疑"})
    await repo.save_chapter({"id": "ch-c", "book_id": "book-c", "title": "第一章", "order_no": 1})
    await repo.save_scene({
        "id": "sc-c", "chapter_id": "ch-c", "title": "雨夜",
        "stage_desc": "雨夜巷口", "goal": "甩掉追踪者", "content_desc": "沈拂在雨里走",
        "final_prose": "夹克内袋那枚金属徽章震了一下，他靠近异常者才会这样。",
    })
    await repo.save_character({"id": "c-sf", "book_id": "book-c", "name": "沈拂",
                               "spec_json": '{"summary": "前档案管理员", "voice": "短句"}'})
    def state(i, kind, name, value, key, edited=False):
        return {"id": i, "book_id": "book-c", "kind": kind, "name": name, "key": key,
                "value": value, "scene_no": 1, "source_event_id": "TEST",
                "previous_value": "", "edited": edited, "ts": 1}
    await repo.save_world_state(state("ws-item", "item", "金属徽章", "携带", "item:沈拂:金属徽章"))
    await repo.save_world_state(state("ws-term", "term", "异常者", "异常者", "term:通用:异常者"))
    await repo.save_world_state(state("ws-num", "numeric", "他:与撑黑伞人影距离", "10米", "num:沈拂:距离", True))
    await repo.save_world_state(state("ws-dirty", "character", "他", "离开顶层", "char:他:位置"))
    return service


@pytest.mark.asyncio
async def test_draft_context_selects_relevant_and_dedupes_ledger(svc):
    """集成：item/term 进得来（B48）· 记忆块不再重复账本（B49）· 代词脏行被裁且留痕（B50）。"""
    ctx = await svc._draft_context("sc-c")
    names = {w["name"] for w in ctx["world_states"]}
    assert "金属徽章" in names and "异常者" in names        # B48
    assert "他:与撑黑伞人影距离" in names                   # 作者手改：必须留
    assert "沈拂" not in names                              # 该角色无账本行（只有物品/术语/数值）
    assert "世界状态现状" not in ctx["memory"]               # B49：账本只注入一次
    assert any(d["name"] == "他" for d in ctx["dropped"])    # B50：脏行被裁
    assert ctx["existing"].startswith("夹克内袋")


@pytest.mark.asyncio
async def test_prose_draft_uses_new_context(svc):
    """无 LLM 仍返回空串 + writer 审计（回归：重构 _draft_context 没改坏老行为）。"""
    out = await svc.prose_draft("sc-c")
    assert out["text"] == ""
    notes = await svc.list_prose_notes("sc-c")
    assert len(notes) == 1 and notes[0]["kind"] == "writer"

# ---------------------------------------------------------------- 写手流式（v1.44）
@pytest.mark.asyncio
async def test_prose_draft_stream_yields_tokens_and_saves_note(svc, monkeypatch):
    """流式写手：逐 token 出（前端据此边收边写）+ 收尾落 writer 审计。"""
    import app.services.service as service_mod

    class _StreamLLM:
        available = True

        async def chat_stream(self, prompt, temperature=0.8, model=None):
            for ch in ["雨", "落", "下", "来"]:
                yield ch

    monkeypatch.setattr(service_mod, "llm_client", _StreamLLM())
    got = []
    async for tok in svc.prose_draft_stream("sc-c"):
        got.append(tok)
    assert got == ["雨", "落", "下", "来"]
    notes = await svc.list_prose_notes("sc-c")
    assert len(notes) == 1
    assert notes[0]["kind"] == "writer" and notes[0]["after"] == "雨落下来"
    assert "流式中断" not in notes[0]["suggestion"]


@pytest.mark.asyncio
async def test_prose_draft_stream_saves_partial_on_break(svc, monkeypatch):
    """流式中断：已生成部分**必须存下来并如实标注**（LLM 花的钱不能白花，部分成果不能静默丢）。"""
    import app.services.service as service_mod

    class _BrokenLLM:
        available = True

        async def chat_stream(self, prompt, temperature=0.8, model=None):
            yield "半"
            yield "句"
            raise RuntimeError("上游断流")

    monkeypatch.setattr(service_mod, "llm_client", _BrokenLLM())
    got = []
    with pytest.raises(RuntimeError):
        async for tok in svc.prose_draft_stream("sc-c"):
            got.append(tok)
    assert got == ["半", "句"]
    notes = await svc.list_prose_notes("sc-c")
    assert len(notes) == 1 and notes[0]["after"] == "半句"
    assert "流式中断" in notes[0]["suggestion"]


@pytest.mark.asyncio
async def test_prose_draft_stream_llm_unavailable_yields_nothing(svc):
    """LLM 不可用 → 空流且**不落** writer 记录（conftest 已全局把 llm_client 换成不可用 fake）。"""
    got = []
    async for tok in svc.prose_draft_stream("sc-c"):
        got.append(tok)
    assert got == []
    assert await svc.list_prose_notes("sc-c") == []
