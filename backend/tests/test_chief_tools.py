"""主笔工具治理回归（v1.40）：工具入 registry + 权限矩阵强制 + chief 工具对话。

"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.agents.registry import get_tool, TOOLS  # noqa: E402
from app.services.agents.permissions import check_write, matrix  # noqa: E402
from app.services.agents.spec import can_use  # noqa: E402


def test_chief_tools_registered():
    """主笔四工具必须在 registry（v1.40 前根本不存在——对话无法驱动）。"""
    for name in ('outline.plan', 'outline.commit', 'characters.generate', 'memory.write'):
        t = get_tool(name)
        assert t is not None, name
        assert t.group == 'chief'
    # AgentSpec 声明与 registry 对齐
    for name in ('outline.plan', 'outline.commit', 'characters.generate', 'memory.write'):
        assert can_use('chief', name)


def test_chief_write_permissions_enforced():
    """v1.40：chapters/characters/book_memories 三行由 ToolExecutor 强制（owner=chief）。"""
    enforced = {m['target'] for m in matrix() if m['enforced']}
    assert {'chapters', 'characters', 'book_memories', 'scenes:final_prose'} <= enforced
    # 责编不能写 chapters（owner=chief）
    assert check_write('editor', 'chapters') is not None
    # 主笔可以
    assert check_write('chief', 'chapters') is None
    # 作者永远放行
    assert check_write('', 'chapters') is None


def test_chief_cannot_use_editor_tools():
    """主笔不能调责编的正文工具（白名单隔离）。"""
    assert not can_use('chief', 'prose.draft')
    assert not can_use('chief', 'prose.save')


def test_chief_chat_executes_tools():
    """主笔工具对话：LLM 计划 → characters.generate 经 executor 执行。"""
    import pytest
    pytest.importorskip('fastapi.testclient')
    from fastapi.testclient import TestClient
    from app.db.repo import Repo
    from app.api.deps import get_service
    from app.main import app

    shared = Repo(use_db=False)

    async def _fake_repo():
        return shared

    from app.api.deps import get_repo
    # chief_chat_with_tools 依赖 service（含 repo + LLM 客户端）——用依赖覆盖 + 假 LLM
    app.dependency_overrides[get_repo] = _fake_repo
    from app.services.engine import chief_planner as cp_mod
    from app.services.llm.client import client as real_client

    # 计划：生成角色卡（LLM 返回固定计划）
    class FakePlanLLM:
        available = True
        async def call_strong(self, prompt, json_schema=None):
            return {'reply': '好，我来生成角色卡', 'actions': [{'tool': 'characters.generate', 'args': {}, 'why': '占位卡'}]}
        async def call_cheap(self, prompt, json_schema=None):
            return await self.call_strong(prompt, json_schema)
        async def chat_stream(self, prompt, temperature=0.7, model=None):
            if False:
                yield

    import app.services.agents.editor as editor_mod
    # generate_book_characters 内部走 chief_planner.generate_characters（用 llm_client）→
    # 让它也走假 LLM（返回一张有效卡）
    class FakeCardLLM:
        available = True
        async def call_strong(self, prompt, json_schema=None):
            return [{'name': '程未央', 'summary': '记者', 'voice': '短句', 'traits': ['敏锐'],
                     'core_beliefs': ['真'], 'bottom_lines': ['不害人'], 'goals': ['查案']}]
        async def call_cheap(self, prompt, json_schema=None):
            return await self.call_strong(prompt, json_schema)
        async def chat_stream(self, prompt, temperature=0.7, model=None):
            if False:
                yield

    import asyncio

    async def run():
        with TestClient(app) as c:
            bid = c.post('/api/v1/books', json={'title': '主笔工具书', 'genre': '悬疑'}).json()['id']
            # 覆盖 service 依赖（get_service 造的是真 service——但 repo 覆盖已换）
            # monkeypatch LLM
            import app.services.agents.editor as ed
            orig_plan = getattr(ed, 'llm_client')
            ed.llm_client = FakePlanLLM()
            monkey_llm = getattr(cp_mod, 'llm_client')
            cp_mod.llm_client = FakeCardLLM()
            try:
                r = c.post(f'/api/v1/books/{bid}/chief/tools',
                           json={'messages': [{'role': 'user', 'content': '生成角色卡'}]})
                assert r.status_code == 200, r.text
                body = r.json()
                assert '角色卡' in body['reply'] or body['executed']
                ok_exec = [e for e in body['executed'] if e.get('ok')]
                assert ok_exec and ok_exec[0]['tool'] == 'characters.generate'
            finally:
                ed.llm_client = orig_plan
                cp_mod.llm_client = monkey_llm
                app.dependency_overrides.pop(get_repo, None)
    asyncio.run(run())
