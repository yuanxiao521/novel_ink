"""主笔共创对话测试（内存态 + TestClient）：SSE 帧契约 + 无 LLM 回退。

对齐计划 AC-4：POST /books/{id}/chief/chat 以 event: token / data: {delta} 流式返回，
无 LLM 时回退确定性文案并正常结束（event: done）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    from app.db.repo import Repo
    from app.api.deps import get_repo
    from app.main import app

    shared = Repo(use_db=False)

    async def _fake_repo():
        return shared

    app.dependency_overrides[get_repo] = _fake_repo

    with TestClient(app) as c:
        bid = c.post("/api/v1/books", json={"title": "青冥录", "genre": "玄幻"}).json()["id"]
        c._book_id = bid
        yield c, shared

    app.dependency_overrides.clear()


def _client_stream(inner) -> str:
    """TestClient 对 StreamingResponse：返回原始 SSE 文本。"""
    return inner.content.decode("utf-8")


def test_chief_chat_sse_fallback(client):
    """无 LLM（conftest autouse FakeLLM）→ 流式返回确定性文案，帧格式 token/done。"""
    c, _ = client
    bid = c._book_id
    r = c.post(f"/api/v1/books/{bid}/chief/chat",
               json={"messages": [{"role": "user", "content": "帮我设计第一章的开场"}]})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    text = _client_stream(r)

    tokens = [line for line in text.split("\n") if line.startswith("event: token")]
    done = [line for line in text.split("\n") if line.startswith("event: done")]
    assert len(tokens) >= 2, f"预期至少 2 段回退文案 token，实际 {len(tokens)}: {text[:300]}"
    assert len(done) == 1

    # 每帧 data 是 JSON：{"delta": "..."}
    import json as _json
    data_lines = [line for line in text.split("\n") if line.startswith("data: ")]
    payload = _json.loads(data_lines[0][len("data: "):])
    assert set(payload.keys()) <= {"delta"} and isinstance(payload.get("delta"), str)


def test_chief_chat_with_fake_stream(client, monkeypatch):
    """有 fake 流式 LLM → 逐 token 增量渲染（模拟 available=True + stream_cheap_text）。"""
    import importlib

    class _StreamLLM:
        available = True

        async def stream_cheap_text(self, prompt, temperature=0.8, model=None):
            for ch in ["主", "笔", "回", "复"]:
                yield ch

    fake = _StreamLLM()
    # service.chief_chat_stream 内部 `from app.services.llm.client import client as llm_client`
    # 在调用时读取模块属性，故 patch 模块级单例 client 即可（显式 importlib 避免同名坑）
    client_mod = importlib.import_module("app.services.llm.client")
    monkeypatch.setattr(client_mod, "client", fake)

    c, _ = client
    bid = c._book_id
    r = c.post(f"/api/v1/books/{bid}/chief/chat",
               json={"messages": [{"role": "user", "content": "你好"}]})
    assert r.status_code == 200
    text = _client_stream(r)
    # 4 个 token 片段 + 1 个 done
    from re import findall
    deltas = findall(r'"delta": ?"[^"]*"', text)
    assert len(deltas) == 4, f"expected 4 token deltas, got {len(deltas)}: {text}"
    assert text.rstrip().endswith('event: done') or 'event: done' in text