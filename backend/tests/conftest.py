"""pytest 全局配置：默认禁用真实 LLM 调用（无网络、无 token 消耗）。

项目约定：pytest 用 FakeLLM/回退路径；真实 LLM 需 --live（此处暂无 live flag，
需要真调用时在用例内显式 monkeypatch available=True + fake call）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_db_engine():
    """用例级丢弃 app.db.engine 的全局单例 engine / sessionmaker（B17）。

    成因：TestClient 会把 app 跑在自己的事件循环里并懒创建全局 engine；之后跑在
    session loop 里的 DB 用例复用它 → asyncpg 抛 "attached to a different loop"，
    而 repo 的 _query_or_mem 与测试探针都会把异常吞成"DB 不可用" → DB 用例静默
    退化成内存态（假绿或误 skip，实测：单跑 test_db_orm 通过，跟在 test_api 后即 skip）。
    丢弃引用后，每个用例在自己当前的循环里重建引擎，DB 路径才真正被覆盖。
    """
    import app.db.engine as engine_mod

    engine_mod._engine = None
    engine_mod._session_factory = None
    yield


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
    import app.services.agents.editor as editor_mod
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
    monkeypatch.setattr(editor_mod, "llm_client", fake)
    yield fake