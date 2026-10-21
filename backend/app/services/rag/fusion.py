"""混合检索融合（v1.49 · 移植自 agent_project/app/retrieval/hybrid_retriever.py）。

原实现的融合是 **RRF（Reciprocal Rank Fusion）**，不是加权求和：
    rrf_score = 1 / (k + rank)          # k=60（原 rrf_k 默认值）
单路命中取单边分，两路都命中则两分相加。

为什么用 RRF 而不是分数加权：向量距离（0~2）与 ts_rank（0~1）**量纲根本不可比**，
任何加权系数都是拍脑袋；RRF 只用排名，天然免疫量纲问题。

本文件只放纯函数（无 IO、无 DB），便于单测覆盖；DB 访问在 store.py。
"""
from __future__ import annotations

from typing import Iterable

RRF_K = 60

# kind → 权重：写作时"角色/伏笔"错了最伤，前情次之，场景梗概最弱
KIND_WEIGHT: dict[str, float] = {
    "character": 1.25,
    "foreshadow": 1.20,
    "world": 1.10,
    "chapter": 1.10,
    "memory": 1.00,
    "belief": 1.00,
    "scene": 0.95,
}


def rrf_fuse(ranked: dict[str, list[str]], *, k: int = RRF_K,
             weights: dict[str, float] | None = None) -> dict[str, float]:
    """ranked: {通道名: [chunk_id 按相关度降序]}；返回 {chunk_id: 融合分}。

    通道名可以是 "vector" / "keyword"，也可以是任意多个（多查询融合同样适用）。
    weights 作用于"通道"（不是 kind）；kind 权重在 apply_kind_weight 里乘。
    """
    w = weights or {}
    fused: dict[str, float] = {}
    for channel, ids in ranked.items():
        cw = float(w.get(channel, 1.0))
        for rank, cid in enumerate(ids, start=1):
            fused[cid] = fused.get(cid, 0.0) + cw * (1.0 / (k + rank))
    return fused


def apply_kind_weight(fused: dict[str, float], kinds: dict[str, str],
                      weights: dict[str, float] | None = None) -> dict[str, float]:
    """kinds: {chunk_id: kind}；按 KIND_WEIGHT 放大/衰减。"""
    w = weights or KIND_WEIGHT
    out: dict[str, float] = {}
    for cid, score in fused.items():
        out[cid] = score * float(w.get(kinds.get(cid, ""), 1.0))
    return out


def top(ids: Iterable[str], scores: dict[str, float], limit: int) -> list[str]:
    """按分数取前 limit 个 id（稳定排序：同分按 id 保序，便于测试可重复）。"""
    ordered = sorted(ids, key=lambda i: (-scores.get(i, 0.0), i))
    return ordered[:max(0, limit)]
