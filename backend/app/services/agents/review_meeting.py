"""审稿会（D 批）：把「体检 / 润色 / 质检 / 评审」收敛成**一次编排**。

设计依据：docs/正文协作副驾与Agent协作架构.md §11 D 批。
- 作者不用点四次：一次调用跑四步，返回可解释的汇总；
- **评审**（A2 的 score_text）第一次有了身份与入口（落 `kind="reviewer"` 审计，"谁发起"可分辨）；
- 0-token 给出**裁决**，解释"为什么建议回环 / 润色 / 可以直接存"。
"""
from __future__ import annotations

import json
import time
import uuid

TRIGGER_TOTAL = 78      # 与 A2 触发线保持一致，不另立阈值
TRIGGER_DIM = 70


def verdict_of(total: int | None, dims: dict, high: int, risks: int) -> str:
    """0-token 裁决（复用 A2 的触发线）。"""
    if total is None:
        return "未评分（模型未接入）：先看体检 / 质检意见"
    low = [k for k, v in (dims or {}).items() if isinstance(v, int) and v < TRIGGER_DIM]
    if total < TRIGGER_TOTAL or low:
        why = (f"总分 {total} < {TRIGGER_TOTAL}") if total < TRIGGER_TOTAL else ("维度偏低：" + "、".join(f"{k}={dims[k]}" for k in low))
        return f"建议质量回环（{why}）"
    if high:
        return f"有 {high} 条高危问题 → 建议润色后再保存"
    if risks:
        return f"有 {risks} 条风险提示 → 建议人工确认"
    return f"通过（总分 {total}）可直接保存"


async def run_meeting(service, scene_id: str, text: str = "") -> dict:
    scene, _ = await service._scene_and_book(scene_id)
    body = (text or "").strip() or str(scene.get("final_prose") or "")
    if not body:
        return {
            "scene_id": scene_id, "steps": [], "candidate": "",
            "verdict": "正文为空：先让写手出初稿，或切到「编辑」写一段",
        }

    steps: list[dict] = []

    review = await service.prose_review(scene_id, body)
    issues = ((review or {}).get("report") or {}).get("issues") or []
    high = [i for i in issues if str((i or {}).get("severity")) == "high"]
    steps.append({"step": "体检", "issues": len(issues), "high": len(high)})

    candidate = ""
    if issues:
        polish = await service.prose_polish(scene_id, body)
        candidate = str((polish or {}).get("after") or "")
        steps.append({"step": "润色", "changed": bool(candidate)})

    verify = await service.prose_verify(scene_id, body)
    opinion = (verify or {}).get("opinion") or {}
    risks = opinion.get("risks") or []
    steps.append({
        "step": "质检",
        "risks": len(risks),
        "foreshadow": len(opinion.get("foreshadow_updates") or []),
        "beliefs": len(opinion.get("belief_deltas") or []),
    })

    score = await service.prose_quality_score(scene_id, candidate or body)
    total = score.get("total")
    dims = score.get("scores") or {}
    steps.append({"step": "评审", "total": total, "dims": dims})

    verdict = verdict_of(total, dims, len(high), len(risks))

    note_id = ""
    try:
        note_id = f"note-{uuid.uuid4().hex[:10]}"
        await service.repo.save_prose_note({
            "id": note_id, "scene_id": scene_id, "kind": "reviewer", "status": "approved",
            "suggestion": "审稿会：" + " / ".join(
                str(s.get("step")) + (f"({s.get('total')})" if s.get("total") is not None else "") for s in steps
            ) + f" · 裁决：{verdict}",
            "before": body, "after": candidate, "created_by": "reviewer",
            "payload_json": json.dumps({"steps": steps, "verdict": verdict, "total": total}, ensure_ascii=False),
            "ts": int(time.time() * 1000),
        })
    except Exception:  # noqa: BLE001  审计失败不阻断业务（会被 T9 记为降级）
        note_id = ""

    return {
        "scene_id": scene_id, "steps": steps, "review": review, "verify": verify,
        "score": score, "candidate": candidate, "verdict": verdict, "note_id": note_id,
    }
