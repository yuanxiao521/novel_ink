"""T9 降级可观测性单测：降级被记录、且结构性错误可区分（不再纯静默）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db.repo import DEGRADATION, Repo, degradation_status, note_degradation  # noqa: E402


def test_structural_error_is_counted_and_flagged():
    """结构性错误（如列漂移/反序列化失败）必须计入 structural，让人能发现。"""
    from sqlalchemy.exc import ProgrammingError

    before = degradation_status()["structural"]
    note_degradation(ProgrammingError("column beliefs.id does not exist", None, None), "query")
    st = degradation_status()
    assert st["active"] is True
    assert st["structural"] == before + 1
    assert "ProgrammingError" in st["kinds"]
    assert "beliefs.id" in st["last_reason"]


def test_connection_error_is_not_structural():
    """DB 不可达属预期（内存兜底可用）→ 计入 count 但**不**算 structural。"""
    from sqlalchemy.exc import OperationalError

    before = degradation_status()["structural"]
    note_degradation(OperationalError("connection refused", None, None), "query")
    st = degradation_status()
    assert st["structural"] == before
    assert st["count"] > 0


@pytest.mark.asyncio
async def test_query_or_mem_records_degradation():
    """_query_or_mem 吞异常时必须留痕（以前是纯静默）。"""

    class _BrokenFactory:
        def __call__(self):
            raise RuntimeError("boom")

    repo = Repo(use_db=True)
    repo._session_factory = _BrokenFactory()
    before = degradation_status()["count"]
    out = await repo._query_or_mem(lambda s: "never", ["fallback"])
    assert out == ["fallback"]
    assert degradation_status()["count"] == before + 1


def test_health_exposes_degradation():
    """GET /health 暴露 degraded 段（前端据此显示降级横幅）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert "degraded" in body and {"active", "count", "structural", "last_reason"} <= set(body["degraded"])
    assert DEGRADATION["count"] >= 0
