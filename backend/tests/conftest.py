"""pytest 全局配置：默认禁用真实 LLM 调用（无网络、无 token 消耗）。

项目约定：pytest 用 FakeLLM/回退路径；真实 LLM 需 --live（此处暂无 live flag，
需要真调用时在用例内显式 monkeypatch available=True + fake call）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch):
    """autouse：每个测试默认令 LLM client 不可用 / call 返回 None（走确定性回退）。

    覆盖所有引用 LLM client 的模块（含 graph/service，漏掉会导致真调 LLM 网络卡死）：
      - app.services.engine.director / character / chief_planner
      - app.services.engine.graph（sim 回合主循环）
      - app.services.service（主笔共创 SSE）
    """
    import app.services.engine.director as director_mod
    import app.services.engine.character as char_mod
    import app.services.engine.chief_planner as chief_mod
    import app.services.engine.graph as graph_mod
    import app.services.service as service_mod

    class _Off:
        available = False

        async def call_strong(self, prompt, json_schema=None):
            return None

        async def call_cheap(self, prompt, json_schema=None):
            return None

        async def chat_stream(self, prompt, temperature=0.7, model=None):
            if False:
                yield  # 空 token 流（async generator 契约）

    fake = _Off()
    monkeypatch.setattr(director_mod, "llm_client", fake)
    monkeypatch.setattr(char_mod, "llm", fake)
    monkeypatch.setattr(chief_mod, "llm_client", fake)
    monkeypatch.setattr(graph_mod, "llm_client", fake)
    monkeypatch.setattr(service_mod, "llm_client", fake)
    yield fake