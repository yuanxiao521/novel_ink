"""pytest 全局配置：默认禁用真实 LLM 调用（无网络、无 token 消耗）。

项目约定：pytest 用 FakeLLM/回退路径；真实 LLM 需 --live（此处暂无 live flag，
需要真调用时在用例内显式 monkeypatch available=True + fake call）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch):
    """autouse：每个测试默认令 LLM client 不可用 / call 返回 None（走确定性回退）。"""
    import app.services.engine.director as director_mod
    import app.services.engine.character as char_mod
    import app.services.engine.chief_planner as chief_mod

    class _Off:
        available = False

        async def call_strong(self, prompt, json_schema=None):
            return None

        async def call_cheap(self, prompt, json_schema=None):
            return None

    fake = _Off()
    monkeypatch.setattr(director_mod, "llm_client", fake)
    monkeypatch.setattr(char_mod, "llm", fake)
    monkeypatch.setattr(chief_mod, "llm_client", fake)
    yield fake