"""测试隔离配置。

默认（不传 --live）把 LLM 客户端替换成假实现：`available=True` 但 always 返回 None，
让引擎走与真实 LLM 完全一致的回退路径（确定性脚本/护栏），
从而**不发真实网络请求**，回归秒级、可复现、不消耗 token。

传 `--live` 则保留真实 LLM（打 DeepSeek key），用于联调观察真实涌现质量。
"""
from __future__ import annotations

import pytest


class FakeLLM:
    """可用但永远 No-op 的 LLM：返回 None 触发引擎确定性回退，行为与接 LLM 一致。"""

    available = True

    def call_strong(self, prompt: str, json_schema=None):
        return None

    def call_cheap(self, prompt: str, json_schema=None):
        return None


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="打真实 LLM（需 .env 配对 key）；默认用 FakeLLM 走确定性回退",
    )


@pytest.fixture(autouse=True)
def _isolate_llm(request, monkeypatch):
    if request.config.getoption("--live"):
        yield
        return

    fake = FakeLLM()
    # 在共享单例对象上打补丁，而不是替换模块引用：
    # 既能覆盖引擎所有调用点，也不破坏旧测试 `monkeypatch.setattr(llm_mod.client, ...)`
    # 的 mock 方式（同样是作用于同一单例）。
    c = _client_singleton()
    monkeypatch.setattr(c, "call_strong", fake.call_strong)
    monkeypatch.setattr(c, "call_cheap", fake.call_cheap)
    yield


def _client_singleton():
    # 注意别用 `import app.services.llm.client as ...`：
    # `llm/__init__.py` 把 `client` 重绑成了 LLMClient 单例，会取到实例而非模块。
    from app.services.llm.client import client

    return client