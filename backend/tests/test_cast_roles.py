"""角色分层（v1.53）测试：谁进回合、谁只作背景。

为什么值得钉死：这条规则直接决定"涌现演几个人"。
  ① 全进 → 群像开会、张力摊薄、成本线性涨（这就是要修的病）；
  ② 全不进 → 导演台空转（比病更糟）；
  ③ 存量数据没有档位（默认 minor）→ 必须有**诚实的兜底**，不能给空 cast。
"""
from __future__ import annotations

from app.services.engine.cast_roles import (
    CAST_LIMIT, DEFAULT_TIER, PLAYABLE_TIERS, TIERS, TIER_LABEL,
    background_block, clean_tier, default_cast, is_playable,
    normalize_assignments, split_cast, tier_of,
)


def _c(cid: str, name: str, tier: str = "minor") -> dict:
    return {"id": cid, "name": name, "role_tier": tier}


ROSTER = [
    _c("c1", "林澈", "protagonist"),
    _c("c2", "霍岸", "antagonist"),
    _c("c3", "宁秋鹜", "major"),
    _c("c4", "单霁", "major"),
    _c("c5", "祁叔", "major"),
    _c("c6", "掌柜", "minor"),
    _c("c7", "路人甲", "extra"),
]


def test_tiers_are_five_and_labelled():
    assert TIERS == ("protagonist", "major", "antagonist", "minor", "extra")
    assert TIER_LABEL["protagonist"] == "主角" and TIER_LABEL["extra"] == "背景"
    assert PLAYABLE_TIERS == ("protagonist", "antagonist", "major")
    assert CAST_LIMIT == 5 and DEFAULT_TIER == "minor"


def test_tier_of_falls_back_to_minor():
    assert tier_of({"role_tier": "protagonist"}) == "protagonist"
    assert tier_of({"role_tier": "nonsense"}) == "minor"
    assert tier_of({}) == "minor"
    assert is_playable(_c("x", "甲", "major")) is True
    assert is_playable(_c("y", "乙", "extra")) is False


def test_split_cast_keeps_playable_and_caps_at_limit():
    split = split_cast(ROSTER)
    assert len(split["cast"]) <= CAST_LIMIT
    # 主角优先，其次反派，再关键配角
    assert split["cast"][0] == "c1" and split["cast"][1] == "c2"
    # 次要/背景永远不进 cast，只进 background
    assert set(split["background"]) == {"c6", "c7"}
    assert split["dropped"] == []          # 5 位可演角色刚好装进上限


def test_split_cast_reports_dropped_when_over_limit():
    """超出上限的可演角色降级为背景，且**如实列名**（不静默丢人）。"""
    split = split_cast(ROSTER, limit=3)
    assert split["cast"][0] == "c1"
    assert len(split["dropped"]) == 2
    assert "祁叔" in split["dropped"]
    assert any("超出上限" in n for n in split["notes"])


def test_split_cast_uses_related_text_within_same_tier():
    roster = [_c("a", "甲", "major"), _c("b", "乙", "major"), _c("c", "丙", "major")]
    split = split_cast(roster, related_text="乙 出现在本场", limit=2)
    assert "b" in split["cast"], split  # 同档位里"本场相关"优先


def test_split_cast_no_playable_gives_empty_cast():
    split = split_cast([_c("m1", "甲", "minor"), _c("m2", "乙", "extra")])
    assert split["cast"] == [] and len(split["background"]) == 2


def test_default_cast_falls_back_when_nobody_tiered():
    """存量书全是 minor → 不能给空 cast（否则导演台空转），按相关度兜底并明说。"""
    legacy = [_c("l1", "甲"), _c("l2", "乙"), _c("l3", "丙")]
    got = default_cast(legacy, related_text="丙 出场了", limit=2)
    assert got["tiered"] is False
    assert len(got["cast"]) == 2
    assert any("还没分档" in n for n in got["notes"])


def test_default_cast_tiered_path():
    got = default_cast(ROSTER)
    assert got["tiered"] is True
    assert got["cast"][0] == "c1"
    assert "掌柜" not in got["cast_names"]


def test_default_cast_empty_roster():
    got = default_cast([])
    assert got["cast"] == [] and got["notes"] == []


def test_background_block_lists_only_non_playable():
    text = background_block(ROSTER)
    assert "在场背景角色" in text
    assert "掌柜" in text and "路人甲" in text
    assert "林澈" not in text          # 主角不进背景块


def test_background_block_empty_when_all_playable():
    assert background_block(ROSTER[:5]) == ""


def test_clean_tier_accepts_chinese_and_case():
    assert clean_tier("主角") == "protagonist"
    assert clean_tier("ANTAGONIST") == "antagonist"
    assert clean_tier(" 反派 ") == "antagonist"
    assert clean_tier("whatever") == "minor"
    assert clean_tier(None) == "minor"


def test_normalize_assignments_drops_unknown_ids():
    raw = {"assignments": [
        {"id": "c1", "role_tier": "主角", "why": "视角中心"},
        {"id": "not-in-roster", "role_tier": "protagonist"},
        {"id": "c1", "role_tier": "minor"},      # 重复 → 只留第一次
        "garbage",
    ]}
    out = normalize_assignments(raw, ["c1", "c2"])
    assert out == [{"id": "c1", "role_tier": "protagonist", "why": "视角中心"}]


def test_normalize_assignments_tolerates_bad_shape():
    assert normalize_assignments(None, ["c1"]) == []
    assert normalize_assignments({"assignments": "x"}, ["c1"]) == []
