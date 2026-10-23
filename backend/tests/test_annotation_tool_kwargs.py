"""annotation.resolve 工具链回归（v1.73g）。

作者实测：编辑器页点「标记已处理 / 撤销」→
Repo.update_annotation() got an unexpected keyword argument 'annotation_id'。
根因：工具 params 叫 annotation_id，执行器按 **kwargs 传参，而 repo 形参叫 ann_id。
这里用**真工具执行器**（不是 mock repo 假装）锁死 kwargs 调用链。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402


class _Repo:
    """形参与真 Repo 完全一致（annotation_id, patch），记录调用方式。"""

    def __init__(self):
        self.calls: dict = {}

    async def update_annotation(self, annotation_id: str, patch: dict) -> dict | None:
        self.calls["update_annotation"] = {"annotation_id": annotation_id, **patch}
        return {"id": annotation_id, **patch}


from app.services.service import SimulationService  # noqa: E402


class _Svc:
    """最小 service：真 resolve_annotation（混入）+ 假 repo。"""

    def __init__(self, repo):
        self.repo = repo


_Svc.resolve_annotation = SimulationService.resolve_annotation


@pytest.mark.asyncio
async def test_annotation_resolve_tool_passes_kwargs():
    """annotation.resolve → call_tool → service.resolve_annotation → repo(annotation_id, patch) 必须通。"""
    from app.services.agents.executor import call_tool

    repo = _Repo()
    svc = _Svc(repo)
    result = await call_tool(
        svc, "editor", "annotation.resolve",
        {"annotation_id": "ann-x", "status": "handled"},
        confirm=True, scene_id="s-1",
    )
    assert result is not None
    call = repo.calls.get("update_annotation") or {}
    assert call.get("annotation_id") == "ann-x", call
    assert call.get("status") == "handled"
