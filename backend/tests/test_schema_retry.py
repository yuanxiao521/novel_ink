"""主笔阶段③：结构化输出纠错循环（schema 校验 + 重试 + 不落脏数据）。

覆盖 validate_and_retry 工具与三个接入点（plan 骨架 / 灵感卡 / 世界规则）：
- 校验通过 → 返回原文；失败 → 带错误反馈重试；全部失败 → ValueError（plan）/ 回退（cards/rules）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

from app.services.engine.schema_retry import validate_and_retry


class _FakeCheap:
    """可控假 LLM：按顺序吐 result 序列；记录每次收到的 prompt。"""

    def __init__(self, results):
        self.available = True
        self._results = list(results)
        self.calls: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self._results.pop(0) if self._results else None

    async def call_strong(self, prompt, json_schema=None):
        return await self.call_cheap(prompt, json_schema)


def _chapters_ok(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("chapters"), list):
        return "chapters 必须是数组"
    return None


async def test_retry_recovers_after_failures():
    """首次非法 → 带纠错反馈重试 → 第二次合法即返回；调用 2 次且重试 prompt 含错误描述。"""
    llm = _FakeCheap([{"chapters": "bad"}, {"chapters": [{"title": "第一章", "scenes": []}]}])
    out = await validate_and_retry(llm, "p", {"x": 1}, _chapters_ok, retries=2)
    assert len(llm.calls) == 2
    assert out["chapters"][0]["title"] == "第一章"
    assert "chapters 必须是数组" in llm.calls[1]


async def test_all_fail_raises_valueerror():
    """始终非法 → 抛 ValueError，不返回脏数据。"""
    llm = _FakeCheap([{"chapters": "bad"}] * 4)
    with pytest.raises(ValueError, match="仍校验失败"):
        await validate_and_retry(llm, "p", {}, _chapters_ok, retries=2)
    assert len(llm.calls) == 3  # 首次 + 2 次重试


async def test_none_response_retried_then_raises():
    """模型返回 None（无响应）同样进入重试，最终抛错。"""
    llm = _FakeCheap([None] * 5)
    with pytest.raises(ValueError, match="None"):
        await validate_and_retry(llm, "p", {}, _chapters_ok, retries=1)
    assert len(llm.calls) == 2


# ---------------------------------------------------------------- 接入点行为
async def test_plan_skelly_raises_on_invalid(monkeypatch):
    """plan 骨架非法（即使 available）→ ValueError（不落库）。"""
    import app.services.engine.chief_planner as chief

    class _Bad:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            return {"chapters": "broken"}

    monkeypatch.setattr(chief, "llm_client", _Bad())
    with pytest.raises(ValueError):
        await chief.plan_skelly("废材复仇")


async def test_plan_skelly_passes_valid(monkeypatch):
    """plan 骨架合法 → 原样返回。"""
    import app.services.engine.chief_planner as chief

    plan = {"chapters": [{"title": "第一章 秘境", "scenes": [{"title": "秘境入口"}]}]}

    class _Good:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            return plan

    monkeypatch.setattr(chief, "llm_client", _Good())
    out = await chief.plan_skelly("废材复仇")
    assert out == plan


async def test_generate_cards_falls_back(monkeypatch):
    """灵感卡始终非法 → 回退模板卡（不抛错）。"""
    import app.services.engine.chief_planner as chief

    class _Bad:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            return "not a list"

    monkeypatch.setattr(chief, "llm_client", _Bad())
    cards = await chief.generate_cards("废材复仇")
    assert len(cards) > 0
    assert cards[0]["title"]  # 来自 _FALLBACK_CARDS


async def test_parse_world_rules_returns_empty_on_invalid(monkeypatch):
    """规则解析始终非法 → 返回 []（不阻塞提交），不抛错。"""
    import app.services.engine.chief_planner as chief

    class _Bad:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            return {"concept": "缺 constraint"}

    monkeypatch.setattr(chief, "llm_client", _Bad())
    rules = await chief.parse_world_rules("## 规则\n筑基不能瞬移")
    assert rules == []