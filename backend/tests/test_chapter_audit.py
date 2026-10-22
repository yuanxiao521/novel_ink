"""章级骨架遵循度审计（v1.67 · prose.audit）测试。

边界（这批最关键的一条）：**只读** —— 主笔能看到正文，但一个字都不许改。
所以测试既验"能看出偏离"，也验"没有任何写正文的路径"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.services.engine.chapter_audit import (  # noqa: E402
    checks_zero_token, normalize_audit, render_audit, validate_audit,
)


def _chapter(**kw) -> dict:
    base = {"id": "c1", "order_no": 3, "summary": "林澈拿到路引并第一次看见界墙。",
            "tension_curve": "压→转→扬", "word_target": 3000}
    base.update(kw)
    return base


def _codes(checks: list[dict]) -> list[str]:
    return [c["code"] for c in checks]


# ---------------------------------------------------------------- 0-token 判定
def test_under_and_over_word_target():
    under = checks_zero_token(chapter=_chapter(), body="一" * 1000, scenes=[])
    assert "words_under" in _codes(under)
    over = checks_zero_token(chapter=_chapter(), body="一" * 5000, scenes=[])
    assert "words_over" in _codes(over)
    ok = checks_zero_token(chapter=_chapter(), body="一" * 3000, scenes=[])
    assert "words_under" not in _codes(ok) and "words_over" not in _codes(ok)


def test_missing_goal_and_target_and_curve():
    codes = _codes(checks_zero_token(chapter=_chapter(summary="", tension_curve="", word_target=0),
                                     body="一" * 100, scenes=[]))
    assert "no_goal" in codes and "no_target" in codes and "no_curve" in codes


def test_empty_body_is_high():
    checks = checks_zero_token(chapter=_chapter(), body="", scenes=[])
    empty = [c for c in checks if c["code"] == "empty"]
    assert empty and empty[0]["level"] == "high"


def test_beats_unmet_is_flagged_from_scene_cfg():
    scenes = [{"title": "站台", "plan_cfg_json": json.dumps(
        {"beats_status": {"ok": False, "missed_texts": ["林澈必须听见第一声试钟"]}},
        ensure_ascii=False)}]
    checks = checks_zero_token(chapter=_chapter(), body="一" * 3000, scenes=scenes)
    hit = [c for c in checks if c["code"] == "beats_unmet"]
    assert hit and "第一声试钟" in hit[0]["detail"]
    assert hit[0]["level"] == "high"


def test_scene_not_merged_flag():
    scenes = [{"title": "站台", "final_prose": "雨水从破洞的铁皮顶浇下来。"}]
    checks = checks_zero_token(chapter=_chapter(), body="正文里没有那段", scenes=scenes)
    assert "scene_not_merged" in _codes(checks)
    merged = checks_zero_token(chapter=_chapter(), body="雨天。" + "雨水从破洞的铁皮顶浇下来。",
                              scenes=scenes)
    assert "scene_not_merged" not in _codes(merged)


def test_protagonist_absent():
    checks = checks_zero_token(chapter=_chapter(), body="一" * 3000, scenes=[],
                              protagonist_names=["林澈"])
    assert "protagonist_absent" in _codes(checks)
    assert not [c for c in checks_zero_token(chapter=_chapter(), body="林澈走进雨里。", scenes=[],
                                            protagonist_names=["林澈"])
                if c["code"] == "protagonist_absent"]


def test_volume_without_goal():
    checks = checks_zero_token(chapter=_chapter(), body="一" * 3000, scenes=[],
                              volume={"arc_goal": ""})
    assert "volume_no_goal" in _codes(checks)


# ---------------------------------------------------------------- LLM 结果清洗
def test_validate_and_normalize():
    assert validate_audit({"verdict": "ok", "items": []}) is None
    assert validate_audit({"items": []}) is not None          # 缺 verdict
    assert validate_audit({"verdict": "ok", "items": [{}]}) is not None
    raw = {
        "verdict": "  这一章没完成章目标  ",
        "score": 130,
        "items": [
            {"kind": "goal_missed", "detail": "没写到界墙", "suggest": "补一段", "severity": "low"},
            {"kind": "tension_off", "detail": "全程平推", "severity": "high"},
            {"detail": ""},                                    # 空 detail → 丢
        ],
        "keep": ["林澈的腔调"],
    }
    out = normalize_audit(raw)
    assert out["verdict"] == "这一章没完成章目标"
    assert out["score"] == 100                                  # 裁剪到 0-100
    assert [i["severity"] for i in out["items"]] == ["high", "low"]   # 按严重度排序
    assert out["keep"] == ["林澈的腔调"]
    assert normalize_audit("坏数据")["items"] == []


def test_render_audit_one_pager():
    text = render_audit({
        "checks": [{"level": "high", "detail": "章摘要为空", "code": "no_goal", "suggest": ""}],
        "deviation": {"verdict": "没完成目标", "score": 60,
                      "items": [{"severity": "high", "detail": "没写到界墙", "suggest": "补一段"}],
                      "keep": ["腔调对"]},
    })
    assert "没完成目标" in text and "章摘要为空" in text and "没写到界墙" in text and "腔调对" in text


# ---------------------------------------------------------------- 只读边界 + 服务层
def test_audit_tool_is_read_only_and_chief_only():
    from app.services.agents.registry import get_tool
    from app.services.agents.spec import can_use

    decl = get_tool("prose.audit")
    assert decl is not None
    assert decl.side_effect == "read"      # 只读：不改正文、不改骨架
    assert decl.writes == ""
    assert decl.params == ("chapter_id",)
    assert can_use("chief", "prose.audit")          # 主笔能看正文（新能力）
    assert not can_use("editor", "prose.audit")     # 责编不需要；边界更窄更安全


@pytest.mark.asyncio
async def test_service_audit_runs_without_llm_and_writes_no_prose(monkeypatch):
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    await repo.save_book({"id": "b-a", "title": "审计", "genre": "测试",
                          "status": "planned", "cover_init": "审", "synopsis": ""})
    await repo.save_chapter({"id": "c-a", "book_id": "b-a", "title": "第 1 章",
                             "summary": "林澈拿到路引。", "order_no": 1, "tone": "suspense",
                             "word_target": 1000})
    await repo.save_chapter_prose("c-a", "林澈在雨里捡到一张路引。")
    svc = SimulationService(repo)

    # 强制 LLM 不可用：只跑 0-token（真实环境 LLM 挂了也必须给结论）
    import app.services.service as svc_mod

    monkeypatch.setattr(svc_mod.llm_client, "available", False)
    out = await svc.chapter_prose_audit("c-a")
    assert out["chapter_id"] == "c-a"
    assert isinstance(out["checks"], list) and out["checks"]
    assert out["deviation"] is None
    assert "LLM" in str(out.get("skipped") or "")
    assert out["rendered"]
    # 只读：正文一个字都没变
    assert (await repo.get_chapter("c-a"))["prose_md"] == "林澈在雨里捡到一张路引。"
    # 但结论要留痕（章级审计 note）
    notes = await repo.list_chapter_notes("c-a")
    assert notes and notes[0]["chapter_id"] == "c-a"
