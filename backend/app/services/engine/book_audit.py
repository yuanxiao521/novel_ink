"""整书体检（BookAudit · v1.38）：0-token 派生，零 LLM。

在 S3 全局结构（global_view：R1-R7 结构诊断 + 伏笔网络）之上，补齐"成书前最后一道质检"：
  A. 口音漂移：逐场 per_character_metrics → 同一角色跨场句长/语气词集合漂移（S2 口吻校验的全书视角）
  B. 时间线一致性：world_states 按 scene_no 回放 → time/numeric 值回退（"当前日"倒流 / 关键数字变小）

原则与 global_view 一致：
  - 零新表、零 LLM，全部由既有数据派生；
  - 样本不足不下结论（角色台词 <2 场不判漂移；同一 key 无多值不判倒流）；
  - 每条结论带 evidence 与可跳转定位（scene_id / ws_id）。
"""
from __future__ import annotations

import re

from app.services.engine.style_checks import per_character_metrics

# ---------------------------------------------------------------- 阈值（可调）
LEN_DRIFT_RATIO = 0.45      # 同角色跨场平均句长偏离该书自身基线 ≥45% → 句长漂移
LEN_ABS_MIN = 4.0           # 且绝对差 ≥4 字（避免"8 字 vs 9 字"这种噪声）
DRIFT_SCENES_MIN = 2        # 角色至少在 2 场有台词才判漂移
TIME_PAT = re.compile(r"\d+(?:\.\d+)?")


def _num(s: str) -> float | None:
    """取字符串里第一个数字（'第3日'→3 / '500 两'→500）。无数字 → None。"""
    m = TIME_PAT.search(str(s or ""))
    return float(m.group()) if m else None


def voice_drift_by_scene(scene_texts: list[dict], characters: list[dict]) -> list[dict]:
    """口音漂移：逐场统计各角色句长/语气词 → 跨场对比。

    scene_texts: [{scene_id, order, title, text}]（只含有正文与台词的场）
    返回按角色聚合的漂移报告（含每场明细，便于前端定位）。
    """
    # 场 × 角色矩阵
    rows: list[dict] = []
    for sc in scene_texts:
        for m in per_character_metrics(str(sc.get("text") or ""), characters):
            if m.get("dialogues", 0) > 0:
                rows.append({"scene_id": sc.get("scene_id"), "order": sc.get("order"),
                             "title": sc.get("title") or "", **m})

    by_char: dict[str, list[dict]] = {}
    for r in rows:
        by_char.setdefault(str(r["name"]), []).append(r)

    out: list[dict] = []
    for name, items in sorted(by_char.items()):
        if len(items) < DRIFT_SCENES_MIN:
            continue
        lens = [float(i["avg_len"]) for i in items if i.get("avg_len")]
        base = sum(lens) / len(lens) if lens else 0.0
        # 语气词集合：全书基线 = 并集；某场只出现基线的极小一角 → 腔调趋同嫌疑
        particles_base: set[str] = set()
        for i in items:
            particles_base |= set(i.get("particles") or [])
        drift_scenes = [
            {
                "scene_id": i.get("scene_id"), "order": i.get("order"), "title": i.get("title"),
                "avg_len": i.get("avg_len"), "particles": i.get("particles") or [],
                "len_delta": round(float(i.get("avg_len") or 0) - base, 1),
            }
            for i in items
        ]
        flagged = []
        for i in items:
            d = abs(float(i.get("avg_len") or 0) - base)
            if base > 0 and d / base >= LEN_DRIFT_RATIO and d >= LEN_ABS_MIN:
                flagged.append({
                    "kind": "len_drift", "scene_id": i.get("scene_id"),
                    "order": i.get("order"), "title": i.get("title"),
                    "detail": "平均句长 %.1f vs 自身基线 %.1f（偏离 %.0f%%）→ 句长漂移"
                              % (float(i.get("avg_len") or 0), base, 100 * d / base),
                })
        out.append({
            "name": name, "scenes_count": len(items), "baseline_len": round(base, 1),
            "baseline_particles": sorted(particles_base),
            "scenes": sorted(drift_scenes, key=lambda x: (x.get("order") or 0)),
            "flagged": flagged,
        })
    return out


def timeline_replay(world_states: list[dict]) -> dict:
    """时间线回放：time/numeric 类状态 → 数值倒流检测。

    关键数据现实（v1.38 实现时核对）：账本是 **UNIQUE(book_id,kind,key) 的 upsert**，
    同一 key 永远只有一行——"多行按 scene_no 排序"的想法对真实账本**永远不触发**。
    可靠的数据源是账本自带的 `previous_value`（每次 append 记下的前值）。

    倒流定义：前值与现值都含数字、且 现值数字 < 前值数字
    （适用时间日数 / 距离 / 数量这类单调量；"第3日 → 第1日""30米 → 10米"）。
    作者手改（edited=True）会标注出来——那是作者主动改设定，不算账本 bug。
    """
    flows: list[dict] = []
    rewinds: list[dict] = []
    for w in (world_states or []):
        if w.get("kind") not in {"time", "numeric"}:
            continue
        prev_n = _num(w.get("previous_value"))
        cur_n = _num(w.get("value"))
        chain = [{"ws_id": w.get("id"), "name": w.get("name") or "", "key": w.get("key") or "",
                  "previous": w.get("previous_value") or "", "value": w.get("value") or "",
                  "scene_no": int(w.get("scene_no") or 0), "num": cur_n,
                  "edited": bool(w.get("edited"))}]
        flows.append({"key": w.get("key") or "", "name": w.get("name") or "", "chain": chain})
        if prev_n is not None and cur_n is not None and cur_n < prev_n:
            rewinds.append({
                "key": w.get("key") or "", "name": w.get("name") or "",
                "detail": "「%s」从 %g 变成 %g → 数值倒流%s"
                          % (w.get("name") or w.get("key") or "", prev_n, cur_n,
                             "（作者手改，如非本意请检查）" if w.get("edited") else ""),
                "ws_id": w.get("id"), "scene_no": int(w.get("scene_no") or 0),
                "from": prev_n, "to": cur_n, "edited": bool(w.get("edited")),
            })
    flows.sort(key=lambda f: f["key"])
    return {"flows": flows, "rewinds": rewinds}
