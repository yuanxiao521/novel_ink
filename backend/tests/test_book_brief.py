"""全局脉络 + 骨架体检（v1.50 · 0-token）测试。

为什么值得单测：这两件事决定"骨架好不好"和"主笔掌管全局靠什么"，
而它们全是**确定性规则**（不调 LLM），所以必须能精确断言——
一旦规则写错，主笔会收到错误的缺陷清单，或者更糟：什么都没报（假通过）。
"""
from __future__ import annotations

from app.services.engine.book_brief import (
    MODE_LABEL, build_brief, render_brief, skeleton_check,
)


def _ch(no: int, **kw) -> dict:
    base = {
        "id": f"c{no}", "book_id": "b1", "order_no": no, "title": f"第 {no} 章",
        "summary": "", "prose_md": "", "tone": "action", "tension_curve": "raise",
        "word_target": 3000, "mode": "draft", "production_json": "{}",
    }
    base.update(kw)
    return base


def _brief(chapters, *, scenes=None, characters=None, foreshadows=None,
           world_states=None, beliefs=None) -> dict:
    return build_brief(
        book={"id": "b1", "title": "测试书", "genre": "玄幻", "synopsis": "一句话方向"},
        chapters=chapters, scenes_by_chapter=scenes or {},
        characters=characters or [], foreshadows=foreshadows or [],
        world_states=world_states or [], beliefs=beliefs or [],
    )


def _codes(brief: dict) -> list[str]:
    return [i["code"] for i in brief["issues"]]


# ---------------------------------------------------------------- 脉络本身
def test_brief_counts_and_written_status():
    brief = _brief([_ch(1, prose_md="林澈醒来。" * 40), _ch(2)])
    m = brief["metrics"]
    assert m["chapters"] == 2 and m["written"] == 1 and m["planned"] == 1
    assert brief["chapters"][0]["status"] == "written"
    assert brief["chapters"][1]["status"] == "planned"
    assert m["total_words"] > 0


def test_word_count_uses_chapter_prose_first_not_scenes():
    """字数口径必须与全局一致（B39）：章正文优先，场景聚合回退。"""
    scenes = {"c1": [{"id": "s1", "final_prose": "场景正文" * 100}]}
    with_prose = _brief([_ch(1, prose_md="章正文" * 300)], scenes=scenes)
    assert with_prose["chapters"][0]["chars"] == 900      # 章正文优先（3 字 ×300）
    only_scenes = _brief([_ch(1)], scenes=scenes)
    assert only_scenes["chapters"][0]["chars"] == 400     # 回退到场景聚合（4 字 ×100）


def test_modes_are_counted_and_labelled():
    brief = _brief([_ch(1, mode="draft"), _ch(2, mode="emerge"), _ch(3, mode="hybrid")])
    assert brief["metrics"]["modes"] == {"draft": 1, "emerge": 1, "hybrid": 1}
    labels = {c["mode_label"] for c in brief["chapters"]}
    assert labels == {"直写", "涌现", "混合"} == set(MODE_LABEL.values())


def test_bad_production_json_does_not_break():
    brief = _brief([_ch(1, production_json="{不是 JSON")])
    assert brief["chapters"][0]["hook"] == ""
    assert brief["score"] >= 0


# ---------------------------------------------------------------- 体检规则
def test_no_target_flagged():
    brief = _brief([_ch(1, word_target=0)])
    assert "no_target" in _codes(brief)


def test_no_curve_flagged():
    brief = _brief([_ch(1, tension_curve="")])
    assert "no_curve" in _codes(brief)


def test_emerge_mode_without_rehearse_flagged():
    brief = _brief([_ch(1, mode="emerge")])
    assert "emerge_no_rehearse" in _codes(brief)
    ok = _brief([_ch(1, mode="emerge", production_json='{"must_rehearse": ["s1"]}')])
    assert "emerge_no_rehearse" not in _codes(ok)


def test_foreshadow_no_close_is_high_after_three_written():
    fs = [{"id": "f1", "status": "buried", "text": "玉佩缺口"},
          {"id": "f2", "status": "in_progress", "text": "盲周的残图"}]
    brief = _brief([_ch(i, prose_md="正文" * 50) for i in (1, 2, 3)], foreshadows=fs)
    high = [i for i in brief["issues"] if i["code"] == "fs_no_close"]
    assert high and high[0]["level"] == "high"
    # 只写 1 章时不下这个结论（防误报）
    early = _brief([_ch(1, prose_md="正文" * 50)], foreshadows=fs)
    assert "fs_no_close" not in _codes(early)


def test_foreshadow_overdue_detected_via_expected_close_scene():
    scenes = {"c1": [{"id": "s1"}], "c2": [{"id": "s2"}]}
    fs = [{"id": "f1", "status": "buried", "text": "应在前两章收掉",
           "expected_close_scene": "s2"}]
    brief = _brief([_ch(1, prose_md="正文" * 50), _ch(2, prose_md="正文" * 50)],
                   scenes=scenes, foreshadows=fs)
    overdue = brief["foreshadows"]["overdue"]
    assert len(overdue) == 1 and overdue[0]["due_chapter"] == 2
    assert "fs_overdue" in _codes(brief)


def test_foreshadow_recovery_rate():
    fs = [{"id": "f1", "status": "closed", "text": "a"},
          {"id": "f2", "status": "buried", "text": "b"}]
    brief = _brief([_ch(1)], foreshadows=fs)
    assert brief["foreshadows"]["recovery_rate"] == 0.5
    assert brief["foreshadows"]["open"] == 1


def test_character_gap_detected():
    chapters = [_ch(1, prose_md="林澈出场"),
                _ch(2, prose_md="没有主角"),
                _ch(3, prose_md="没有主角"),
                _ch(4, prose_md="没有主角"),
                _ch(5, prose_md="林澈回来")]
    brief = _brief(chapters, characters=[{"id": "ch1", "name": "林澈"}])
    entry = brief["characters"]["entries"][0]
    assert entry["appears_in"] == [1, 5]
    assert entry["worst_gap"] == 3
    assert "char_gap" in _codes(brief)


def test_character_orphan_flagged_only_after_three_written():
    chapters = [_ch(i, prose_md="无关正文") for i in (1, 2, 3)]
    brief = _brief(chapters, characters=[{"name": "从未出场的人"}])
    assert "char_orphan" in _codes(brief)


def test_score_drops_with_severity():
    clean = _brief([_ch(1, prose_md="正文" * 50)])
    dirty = _brief([_ch(1, word_target=0, tension_curve="")])
    assert clean["score"] == 100
    assert dirty["score"] < clean["score"]


def test_skeleton_check_shape():
    brief = _brief([_ch(1, word_target=0)])
    sk = skeleton_check(brief)
    assert set(sk) == {"score", "issues", "counts", "metrics"}
    assert sk["counts"]["mid"] >= 1


# ---------------------------------------------------------------- prompt 渲染
def test_render_brief_sections_and_budget():
    brief = _brief([_ch(i, prose_md="正文" * 30) for i in range(1, 6)],
                   characters=[{"name": "林澈"}],
                   foreshadows=[{"id": "f1", "status": "buried", "text": "玉佩"}],
                   world_states=[{"id": "w1"}], beliefs=[{"id": "b1"}])
    text = render_brief(brief, budget=1200)
    assert "【全局脉络" in text
    assert "章节：" in text and "伏笔：" in text and "角色出场：" in text
    assert len(text) <= 1200


def test_render_brief_truncates_many_chapters():
    brief = _brief([_ch(i) for i in range(1, 31)])
    text = render_brief(brief, max_chapters=5)
    assert "另有 25 章" in text
