"""章级生产路径裁决（v1.52 · 0-token）测试。

为什么测：这个函数决定"作者点按钮时该去哪、写手拿到什么素材"。算错的两个后果都很贵——
① 该推演的章直接被直写 → 写手凭空编（涌现模式白做）；② 已经推演完却还拦着 → 作者被卡住。
"""
from __future__ import annotations

from app.services.engine.chapter_production import (
    MODE_LABEL, plan_production, rehearsal_block, required_scenes,
)


def _ch(mode: str = "draft", **prod) -> dict:
    import json

    return {"id": "c1", "title": "第 1 章", "mode": mode,
            "production_json": json.dumps(prod, ensure_ascii=False)}


SCENES = [{"id": "s1", "title": "场一", "scene_summary": "林澈拿到路引",
           "final_prose": ""},
          {"id": "s2", "title": "场二", "scene_summary": "", "final_prose": ""}]


# ---------------------------------------------------------------- 裁决
def test_draft_needs_no_rehearsal():
    p = plan_production(chapter=_ch("draft"), scenes=SCENES, turns_by_scene={})
    assert p["mode_label"] == "直写"
    assert p["next_action"] == "draft" and p["can_write"] is True
    assert p["required"] == [] and p["pending"] == []


def test_emerge_without_must_requires_all_scenes():
    p = plan_production(chapter=_ch("emerge"), scenes=SCENES, turns_by_scene={})
    assert p["required"] == ["s1", "s2"] and p["pending"] == ["s1", "s2"]
    assert p["next_action"] == "rehearse" and p["can_write"] is False
    assert any("全部场" in n for n in p["notes"])


def test_emerge_with_must_only_requires_those():
    p = plan_production(chapter=_ch("emerge", must_rehearse=["s2"]),
                        scenes=SCENES, turns_by_scene={})
    assert p["required"] == ["s2"] and p["pending"] == ["s2"]


def test_emerge_satisfied_allows_writing():
    p = plan_production(chapter=_ch("emerge", must_rehearse=["s1"]),
                        scenes=SCENES, turns_by_scene={"s1": 4})
    assert p["pending"] == [] and p["can_write"] is True
    assert p["next_action"] == "draft" and "渲染" in p["why"]
    assert p["turns_total"] == 4


def test_hybrid_only_cares_about_must_scenes():
    p = plan_production(chapter=_ch("hybrid", must_rehearse=["s2"]),
                        scenes=SCENES, turns_by_scene={"s1": 9})
    assert p["required"] == ["s2"] and p["pending"] == ["s2"]
    assert p["can_write"] is False


def test_hybrid_without_must_is_effectively_draft():
    p = plan_production(chapter=_ch("hybrid"), scenes=SCENES, turns_by_scene={})
    assert p["can_write"] is True
    assert any("等同直写" in n for n in p["notes"])


def test_emerge_without_scenes_is_manual():
    p = plan_production(chapter=_ch("emerge"), scenes=[], turns_by_scene={})
    assert p["next_action"] == "manual" and p["can_write"] is False
    assert "拆场" in p["why"]


def test_invalid_mode_falls_back_to_draft_with_note():
    p = plan_production(chapter=_ch("nonsense"), scenes=SCENES, turns_by_scene={})
    assert p["mode"] == "draft" and p["can_write"] is True
    assert any("非法" in n for n in p["notes"])


def test_scene_rows_carry_turns_and_finalized():
    scenes = [{"id": "s1", "title": "场一", "final_prose": "已成稿正文"}]
    p = plan_production(chapter=_ch("draft"), scenes=scenes,
                        turns_by_scene={"s1": 3})
    row = p["scenes"][0]
    assert row["turns"] == 3 and row["finalized"] is True


def test_required_scenes_helper():
    assert required_scenes("emerge", [], ["s1", "s2"]) == ["s1", "s2"]
    assert required_scenes("emerge", ["s1"], ["s1", "s2"]) == ["s1"]
    assert required_scenes("hybrid", [], ["s1"]) == []
    assert required_scenes("draft", ["s1"], ["s1"]) == []


# ---------------------------------------------------------------- 写手素材
def test_rehearsal_block_uses_summary_then_prose():
    scenes = [
        {"id": "s1", "title": "场一", "scene_summary": "林澈拿到路引", "final_prose": ""},
        {"id": "s2", "title": "场二", "scene_summary": "", "final_prose": "他推门出去。" * 30},
        {"id": "s3", "title": "场三", "scene_summary": "", "final_prose": ""},
    ]
    text = rehearsal_block(scenes=scenes, turns_by_scene={"s1": 5, "s2": 2})
    assert "【已推演的场" in text
    assert "场一：已推演 5 回合｜场摘要：林澈拿到路引" in text
    assert "场二：已推演 2 回合｜已定稿正文节选：" in text
    assert "场三" not in text          # 没推演没正文的场不该混进来


def test_rehearsal_block_empty_when_nothing_to_render():
    """没有推演、也没有场摘要/正文 → 整块不出现（别给写手塞空标题）。"""
    bare = [{"id": "s9", "title": "场九", "scene_summary": "", "final_prose": ""}]
    assert rehearsal_block(scenes=bare, turns_by_scene={}) == ""
    assert rehearsal_block(scenes=[], turns_by_scene={}) == ""


def test_rehearsal_block_keeps_summary_even_without_turns():
    """场摘要是"已确定的事实"，即使回合数为 0 也该给写手（比如旧数据/补录）。"""
    text = rehearsal_block(scenes=SCENES, turns_by_scene={})
    assert "场摘要：林澈拿到路引" in text
    assert "已推演 0" not in text        # 没有回合就别谎报回合数


def test_mode_labels_are_stable():
    assert MODE_LABEL == {"draft": "直写", "emerge": "涌现", "hybrid": "混合"}
