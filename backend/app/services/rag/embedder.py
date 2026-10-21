"""向量通道（v1.49 · 可选 · 移植自 agent_project/app/ingestion/embedder.py）。

原实现走 DashScope OpenAI 兼容的 `text-embedding-v4`（dim=1024），HTTP 调用、无本地推理。
这里保持同样的协议，但把「有没有向量」做成**运行时可选**：
  - .env 配了 NOVEL_EMBED_BASE_URL + NOVEL_EMBED_API_KEY 且维度 = 1024 → 混合检索（向量+关键词 RRF）
  - 没配 / 调用失败 / 维度不符 → **纯关键词检索**（zhparser 中文分词），功能照跑

为什么必须可选：embedding 是外部付费服务，账号欠费/断网时不能把「写小说」整条链路带死；
而中文关键词检索对「人名/地名/术语/伏笔关键词」本来就很好用（见 docs 里的实测）。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

VECTOR_DIM = 1024          # 与 mem_chunks.embedding vector(1024) 必须一致

_STATE: dict[str, Any] = {"calls": 0, "failures": 0, "ok": False,
                           "last_error": "", "last_at": 0, "embedded": 0}


def available() -> bool:
    """配了 key/base 且维度对得上才启用向量通道。"""
    return bool(settings.embed_api_key and settings.embed_base_url) and \
        int(settings.embed_dim or 0) == VECTOR_DIM


def status() -> dict[str, Any]:
    """给 /health 看：为什么没开向量（配置缺失 / 维度不符 / 最近失败原因）。"""
    reason = ""
    if not settings.embed_api_key or not settings.embed_base_url:
        reason = "未配置 NOVEL_EMBED_BASE_URL / NOVEL_EMBED_API_KEY（当前纯关键词检索）"
    elif int(settings.embed_dim or 0) != VECTOR_DIM:
        reason = f"embed_dim={settings.embed_dim} 与表维度 {VECTOR_DIM} 不一致"
    return {"available": available(), "model": settings.embed_model,
            "dim": int(settings.embed_dim or 0), "reason": reason, **_STATE}


async def aembed(texts: list[str]) -> list[list[float]] | None:
    """批量取向量；**任何失败都返回 None**（调用方降级为纯关键词，不抛异常）。"""
    items = [str(t or "")[:2000] for t in (texts or [])]
    if not items or not available():
        return None
    batch = max(1, int(settings.embed_batch or 10))
    out: list[list[float]] = []
    url = settings.embed_base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {settings.embed_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for i in range(0, len(items), batch):
                chunk = items[i:i + batch]
                payload = {"model": settings.embed_model, "input": chunk,
                           "dimensions": VECTOR_DIM, "encoding_format": "float"}
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
                data = r.json().get("data") or []
                if len(data) != len(chunk):
                    raise RuntimeError(f"返回条数不符：{len(data)} != {len(chunk)}")
                for d in data:
                    v = list(d.get("embedding") or [])
                    if len(v) != VECTOR_DIM:
                        raise RuntimeError(f"维度不符：{len(v)} != {VECTOR_DIM}")
                    out.append([float(x) for x in v])
    except Exception as e:  # noqa: BLE001  embedding 失败必须降级而不是打断重建
        _STATE.update({"calls": _STATE["calls"] + 1, "failures": _STATE["failures"] + 1,
                       "ok": False, "last_error": f"{type(e).__name__}: {e}"[:200]})
        logger.warning("[rag] embedding 失败，降级为纯关键词：%s", e)
        return None
    _STATE.update({"calls": _STATE["calls"] + 1, "ok": True, "last_error": "",
                   "embedded": _STATE["embedded"] + len(out)})
    return out
