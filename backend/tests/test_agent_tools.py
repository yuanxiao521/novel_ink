"""P2 · A 批回归：工具层（Registry/Executor）+ 感知包 + 责编对话 + 批注 CRUD。

设计依据：docs/正文协作副驾与Agent协作架构.md §6/§7/§8/§11。
约定：默认走 FakeLLM 回退（conftest autouse）；批注落库用例在 DB 不可用时 skip（不静默假绿）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.services.agents import editor as editor_mod  # noqa: E402
from app.services.agents.executor import ToolDenied, call_tool  # noqa: E402
from app.services.agents.registry import TOOLS, get_tool, list_tools  # noqa: E402
from app.services.agents.spec import CHARACTER, EDITOR, can_use, get_spec  # noqa: E402


# ---------------------------------------------------------------- 桩（不碰 DB/网络）


class _FakeRepo:
    """最小 repo 桩：perceive_scene / 审计写入用。"""

    def __init__(self):
        self.notes: list[dict] = []

    async def get_scene(self, scene_id: str) -> dict:
        return {
            "id": scene_id, "title": "雨夜书房", "goal": "确认残页真伪",
            "stage_desc": "书房·夜·雨", "content_desc": "陈默与李文对峙",
            "final_prose": "第一段文字。\n\n第二段文字，με 他低声道。",
        }

    async def list_annotations(self, scene_id: str, status: str = "") -> list[dict]:
        return [{"id": "ann-1", "para_index": 2, "quote": "第二段文字", "note": "这句不像他说的", "status": "open"}]

    async def list_prose_notes(self, scene_id: str, limit: int = 100) -> list[dict]:
        return [{"kind": "editor", "status": "pending", "suggestion": "AI 味扫描：命中 1 条"}]

    async def list_characters_by_scene(self, scene_id: str) -> list[dict]:
        return [{"name": "陈默", "spec": {"summary": "书房主人", "voice": "话不多"}}]

    async def save_prose_note(self, data: dict) -> None:
        self.notes.append(data)

    async def update_annotation(self, ann_id: str, patch: dict) -> dict | None:
        return {"id": ann_id, **patch}

    async def list_annotations_all(self, scene_id: str) -> list[dict]:
        return []


class _FakeService:
    def __init__(self):
        self.repo = _FakeRepo()
        self.calls: list[str] = []

    async def prose_ai_tone(self, scene_id: str, text: str) -> dict:
        self.calls.append("prose_ai_tone")
        return {"report": {"hits": []}}

    async def save_scene_prose(self, scene_id: str, text: str) -> dict:
        self.calls.append("save_scene_prose")
        return {"word_count": len(text)}


# ---------------------------------------------------------------- Registry / Spec


async def test_registry_has_declared_side_effects():
    tools = {t["name"]: t for t in list_tools()}
    assert "prose.save" in tools and tools["prose.save"]["needs_confirm"] is True
    assert tools["prose.save"]["side_effect"] == "destructive"
    assert tools["prose.scan_tone"]["side_effect"] == "read"
    assert get_tool("nope") is None


async def test_spec_whitelist_boundaries():
    assert can_use("editor", "prose.draft") is True
    assert can_use("editor", "outline.commit") is False        # 责编不能改骨架
    assert can_use("character", "prose.save") is False          # 角色不能写正文
    assert CHARACTER.can_initiate_tasks is False                # 硬边界
    assert EDITOR.scope == "scene" and get_spec("editor") is EDITOR
    assert can_use("", "prose.save") is True                    # 空 agent = 作者直调

    # ---------- 感知包（PerceptPacket） ----------


async def test_perceive_scene_is_structured_and_traceable():
    repo = _FakeRepo()
    packet = await editor_mod.perceive_scene(repo, "scene-1")
    kinds = [b["kind"] for b in packet["blocks"]]
    assert kinds == ["scene", "characters", "annotations", "draft", "recent_notes"]
    assert packet["scope"] == "scene" and packet["who"]["id"] == "editor"
    # 结构化：可直接断言"看到了什么"
    anns = next(b for b in packet["blocks"] if b["kind"] == "annotations")
    assert anns["items"][0]["para_index"] == 2
    chars = next(b for b in packet["blocks"] if b["kind"] == "characters")
    assert chars["items"][0]["name"] == "陈默"
    # 裁剪留痕字段存在（missing scene 时才有内容，但字段必须永远在）
    assert "dropped" in packet
    summary = editor_mod.packet_summary(packet)
    assert summary["counts"]["annotations"] == 1 and summary["scope"] == "scene"


async def test_para_index_of_locates_paragraph():
    text = "第一段。\n\n第二段文字，他低声道。\n\n第三段。"
    assert editor_mod.para_index_of(text, "第二段文字") == 2
    assert editor_mod.para_index_of(text, "不存在的句子") == 0


async def test_editor_injects_draft_text_into_tool_args(monkeypatch):
    """模型通常不会复述正文 → 缺 text 时由感知包回填（真 LLM 实测踩到过）。"""
    svc = _FakeService()

    class _PlanLLM:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            return {"reply": "扫一下。", "actions": [{"tool": "prose.scan_tone", "args": {}, "why": "自查"}]}

        async def call_strong(self, prompt, json_schema=None):
            return None

    monkeypatch.setattr(editor_mod, "llm_client", _PlanLLM())
    out = await editor_mod.editor_chat(svc, "scene-1", "扫一遍")
    assert out["executed"][0]["ok"] is True, out["executed"]
    assert svc.calls == ["prose_ai_tone"]


async def test_validate_plan_contract():
    assert editor_mod._validate_plan({"reply": "好"}) is None
    assert editor_mod._validate_plan({"reply": ""}) is not None
    assert editor_mod._validate_plan([]) is not None


# ---------------------------------------------------------------- Executor


async def test_executor_denies_unknown_and_cross_agent():
    with pytest.raises(ToolDenied):
        await call_tool(None, "editor", "nope.tool", {})
    with pytest.raises(ToolDenied):
        await call_tool(None, "character", "prose.scan_tone", {"scene_id": "s", "text": "t"})
    with pytest.raises(ToolDenied):
        await call_tool(None, "editor", "prose.scan_tone", {"scene_id": "s"})   # 缺 text


async def test_executor_requires_confirm_for_destructive():
    with pytest.raises(ToolDenied) as e:
        await call_tool(None, "editor", "prose.save", {"scene_id": "s", "text": "t"})
    assert "确认" in str(e.value)


async def test_executor_runs_tool_and_writes_audit():
    svc = _FakeService()
    res = await call_tool(svc, "editor", "prose.scan_tone", {"text": "正文"}, who="author:editor", scene_id="scene-1")
    assert res.ok and svc.calls == ["prose_ai_tone"]
    assert res.note_id.startswith("note-")
    note = svc.repo.notes[0]
    assert note["kind"] == "tool" and note["created_by"] == "author:editor"
    assert "prose.scan_tone" in note["suggestion"]


async def test_executor_allows_confirmed_destructive():
    svc = _FakeService()
    res = await call_tool(svc, "editor", "prose.save", {"text": "正文"}, confirm=True, scene_id="scene-1")
    assert res.ok and svc.calls == ["save_scene_prose"] and res.side_effect == "destructive"


# ---------------------------------------------------------------- 责编对话


async def test_editor_chat_llm_off_is_honest():
    svc = _FakeService()
    out = await editor_mod.editor_chat(svc, "scene-1", "把这段改得像陈默")
    assert out["actions"] == [] and out["executed"] == []
    assert "模型未接入" in out["reply"]                     # 不静默：如实告知
    assert out["percept"]["counts"]["annotations"] == 1     # 感知包照常给出


async def test_editor_chat_executes_plan_with_fake_llm(monkeypatch):
    svc = _FakeService()

    class _PlanLLM:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            assert "责编" in prompt and "可用工具" in prompt
            return {"reply": "先扫一遍 AI 味。", "actions": [
                {"tool": "prose.scan_tone", "args": {"text": "正文"}, "why": "自查"},
                {"tool": "outline.commit", "args": {}, "why": "越权试探"},
                {"tool": "prose.save", "args": {"text": "正文"}, "why": "想直接落库"},
            ]}

        async def call_strong(self, prompt, json_schema=None):
            return None

    monkeypatch.setattr(editor_mod, "llm_client", _PlanLLM())
    out = await editor_mod.editor_chat(svc, "scene-1", "看看有没有 AI 味")
    assert out["reply"] == "先扫一遍 AI 味。"
    # 白名单裁剪：outline.commit（主笔的活）被剔除；prose.save 合法但**执行时**要作者确认
    assert [a["tool"] for a in out["actions"]] == ["prose.scan_tone", "prose.save"]
    executed = {e["tool"]: e for e in out["executed"]}
    assert executed["prose.scan_tone"]["ok"] is True
    assert executed["outline.commit"]["ok"] is False                     # 越权 → 如实回报
    assert executed["prose.save"]["ok"] is False                         # 破坏性 → 要作者确认
    assert "确认" in executed["prose.save"]["error"]


# ---------------------------------------------------------------- 批注（真实 DB）


@pytest.mark.skipif(not __import__("app.config", fromlist=["settings"]).settings.persist,
                    reason="persist=False，走内存态")
async def test_annotation_crud_persists():
    """批注落库 + 状态机（对 seed 场景 scene-betrayal-night）。"""
    from app.config import settings
    from app.db.repo import Repo

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text as _sql

    eng = create_async_engine(settings.dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with eng.connect() as conn:
            await conn.execute(_sql("SELECT 1"))
    except Exception:  # noqa: BLE001
        await eng.dispose()
        pytest.skip("DB 不可用")
    await eng.dispose()

    repo = Repo()
    scene_id = "scene-betrayal-night"
    ann_id = "ann-test-p2"
    await repo.save_annotation({
        "id": ann_id, "scene_id": scene_id, "para_index": 2, "quote": "第二段",
        "note": "这句不像他说的", "status": "open", "created_by": "author",
        "handled_by_note_id": "", "ts": 1,
    })
    try:
        rows = await repo.list_annotations(scene_id, status="open")
        assert any(r["id"] == ann_id for r in rows), rows
        got = await repo.get_annotation(ann_id)
        assert got and got["para_index"] == 2 and got["status"] == "open"

        upd = await repo.update_annotation(ann_id, {"status": "handled", "handled_by_note_id": "note-x"})
        assert upd and upd["status"] == "handled"
        assert all(r["id"] != ann_id for r in await repo.list_annotations(scene_id, status="open"))
    finally:
        await repo.delete_annotation(ann_id)
    assert await repo.get_annotation(ann_id) is None


pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------- 任务总线（A+）


async def test_task_initiator_boundary():
    """角色 agent 不允许发起任务——白名单 + AgentSpec 双重校验。"""
    from app.services.agents.tasks import TaskDenied, check_initiator

    check_initiator("author")
    check_initiator("editor")
    check_initiator("chief")
    for bad in ("character", "stage_manager", "writer", "someone"):
        with pytest.raises(TaskDenied):
            check_initiator(bad)


async def test_task_create_and_transition_rules():
    from app.services.agents.tasks import create_task, transition

    class _Repo:
        def __init__(self):
            self.rows: dict = {}
            self.msgs: list = []

        async def save_task(self, data):
            self.rows[data["id"]] = data

        async def get_task(self, tid):
            return self.rows.get(tid)

        async def update_task(self, tid, patch):
            self.rows[tid] = {**self.rows[tid], **patch}
            return self.rows[tid]

        async def save_message(self, data):
            self.msgs.append(data)

    repo = _Repo()
    t = await create_task(repo, from_agent="editor", to_agent="stage_manager", kind="rehearsal",
                          goal="先演一遍", scene_id="scene-1", book_id="book-1")
    assert t["status"] == "submitted"
    assert repo.msgs[0]["role"] == "request"

    # 合法流转：submitted → input-required（等作者确认）
    await transition(repo, t["id"], "input-required", note="要不要演？")
    assert repo.rows[t["id"]]["status"] == "input-required"

    # 非法流转：input-required → submitted（不能回退）
    with pytest.raises(Exception):
        await transition(repo, t["id"], "submitted")

    # 终态不可再动
    await transition(repo, t["id"], "completed")
    with pytest.raises(Exception):
        await transition(repo, t["id"], "working")

    # 未知类型/状态
    with pytest.raises(Exception):
        await create_task(repo, from_agent="author", kind="nope")
    with pytest.raises(Exception):
        await transition(repo, t["id"], "nope")


async def test_write_matrix_is_draft_but_complete():
    from app.services.agents.permissions import matrix

    rows = matrix()
    owners = {r["target"]: r["owner"] for r in rows}
    assert owners["beliefs"] == "bookkeeping"          # 账本只有记账写
    assert owners["prose_notes"] == "executor"         # 审计只有 executor 写
    assert owners["scenes:final_prose"] == "editor"    # 正文归责编
    assert owners["chapters"] == "chief"               # 结构归主笔
    assert all(r["enforced"] is False for r in rows)   # A+ 只出草案，不强制


async def test_specs_expose_capabilities():
    from app.services.agents.spec import SPECS

    assert SPECS["character"].can_initiate_tasks is False
    assert SPECS["stage_manager"].can_initiate_tasks is False
    assert SPECS["editor"].can_initiate_tasks is True
    assert "prose.save" in SPECS["editor"].needs_confirm
    assert "outline.commit" in SPECS["chief"].needs_confirm

