"""S3 全局结构与张力派生（0-token · 纯函数 · 无 IO/无 LLM）。

输入由 service 层装配（章/场景/回合归档/伏笔），本模块只做**派生 + 诊断**：
  · 章级张力：tension_avg / tension_peak / trend / turns / prose_chars
  · 全书曲线：mean/std/peak_chapter/flat_chapters
  · 结构诊断：7 条确定性规则（每条带扣分与证据，可解释）
  · 伏笔呼应网络：埋设章 → 期望回收章 + 逾期清单
  · structure_score = 100 - Σ扣分（下限 0）

设计原则（与《写作优化方案-S3全局结构.md》§3 一致）：
- **零新表**：全部由既有数据派生（张力早已随 TurnArchive 归档）；
- **不按 0 计入**：张力 0 视为"未评估"（回退模式），不当作"平淡"，避免误诊；
- **样本不足不下结论**：单章/无张力时不报 R1-R4，只如实提示 R7。
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

# ---------------------------------------------------------------- 阈值（可调）
FLAT_STD = 8.0            # 章间张力标准差低于此值 + 均值低 → R1 平铺
FLAT_MEAN = 45.0
RISE_WINDOW = 3           # 连续 N 章不升 → R2
CLIMAX_TAIL = 1 / 3       # 峰值应落在后 1/3
CLIMAX_RATIO = 0.6        # 末章低于峰值 × 此比例 → R3
BREATH_DROP = 0.15        # 峰值后回落 ≥ 峰值 × 此比例 → 视为有喘息
IMBALANCE_OPEN_MIN = 5    # 未回收 ≥ 此数且 open > closed×2 → R6
IMBALANCE_RATIO = 2
DEDUCT = {"R1": 20, "R2": 12, "R3": 15, "R4": 8, "R5": 15, "R6": 10, "R7": 5}


def _positive(tensions: list[float]) -> list[float]:
    """只认 >0 的张力：0 视为"未评估"（确定性回退模式），不当作平淡。"""
    return [float(t) for t in tensions if float(t or 0) > 0]


def scene_tension(archives: list[dict] | None) -> dict:
    """单场景张力：均值/峰值/末回合趋势。无有效张力 → has=False。"""
    items = archives or []
    vals = _positive([a.get("tension") for a in items])
    trend = "flat"
    for a in reversed(items):
        if a.get("tension_trend"):
            trend = str(a["tension_trend"])
            break
    if not vals:
        return {"has": False, "avg": None, "peak": None, "turns": len(items), "trend": trend}
    return {"has": True, "avg": round(statistics.fmean(vals), 1), "peak": round(max(vals), 1),
            "turns": len(items), "trend": trend}


def chapter_metrics(chapters: list[dict], scenes: list[dict],
                    archives_by_scene: dict[str, list[dict]]) -> list[dict]:
    """章级聚合：张力均值/峰值（场景间取均值/最大）、回合数、正文字数、状态。"""
    scenes_by_ch: dict[str, list[dict]] = {}
    for s in scenes:
        scenes_by_ch.setdefault(str(s.get("chapter_id") or ""), []).append(s)
    out: list[dict] = []
    for ch in sorted(chapters, key=lambda c: c.get("order_no") or 0):
        ch_scenes = scenes_by_ch.get(str(ch.get("id")), [])
        stats = [scene_tension(archives_by_scene.get(str(s.get("id")))) for s in ch_scenes]
        avgs = [st["avg"] for st in stats if st["has"]]
        peaks = [st["peak"] for st in stats if st["has"]]
        prose_chars = sum(len(s.get("final_prose") or "") for s in ch_scenes)
        done = bool(ch_scenes) and all((s.get("final_prose") or "").strip() for s in ch_scenes)
        status = "done" if done else ("draft" if ch_scenes else "planned")
        trend = next((st["trend"] for st in reversed(stats) if st["has"]), "flat")
        out.append({
            "chapter_id": ch.get("id"), "title": ch.get("title") or "",
            "order_no": ch.get("order_no") or 0, "status": status,
            "scenes": len(ch_scenes), "turns": sum(st["turns"] for st in stats),
            "tension_avg": round(statistics.fmean(avgs), 1) if avgs else None,
            "tension_peak": round(max(peaks), 1) if peaks else None,
            "trend": trend, "prose_chars": prose_chars,
        })
    return out


def build_curve(metrics: list[dict]) -> dict:
    """全书曲线摘要：均值/标准差/峰值章/贴地章（不足 2 个有效章则 std=None）。"""
    vals = [(m["tension_avg"], m) for m in metrics if m["tension_avg"] is not None]
    if not vals:
        return {"mean": None, "std": None, "peak_chapter": None, "flat_chapters": [], "valid": 0}
    nums = [v for v, _ in vals]
    peak = max(vals, key=lambda x: x[0])[1]
    return {
        "mean": round(statistics.fmean(nums), 1),
        "std": round(statistics.pstdev(nums), 1) if len(nums) >= 2 else None,
        "peak_chapter": {"chapter_id": peak["chapter_id"], "title": peak["title"],
                         "order_no": peak["order_no"], "tension_avg": peak["tension_avg"]},
        "flat_chapters": [m["order_no"] for m in metrics
                          if m["tension_avg"] is not None and m["tension_avg"] < FLAT_MEAN],
        "valid": len(nums),
    }


def _diag(rule: str, severity: str, title: str, detail: str, evidence: Any = None) -> dict:
    return {"rule": rule, "severity": severity, "deduct": DEDUCT.get(rule, 0),
            "title": title, "detail": detail, "evidence": evidence or []}


def diagnose(metrics: list[dict], curve: dict, foreshadows: list[dict],
             overdue: list[dict], barren_scenes: list[str]) -> list[dict]:
    """7 条确定性诊断。样本不足（有效章 <2）时 R1-R4 一律不报。"""
    out: list[dict] = []
    valid = [m for m in metrics if m["tension_avg"] is not None]
    orders = [m["order_no"] for m in valid]

    if len(valid) >= 3 and curve["std"] is not None:
        if curve["std"] < FLAT_STD and (curve["mean"] or 0) < FLAT_MEAN:
            out.append(_diag("R1", "high", "全书张力平铺",
                             "章间张力波动 %.1f（阈值 %.0f）且均值 %.1f（阈值 %.0f）→ 缺起伏"
                             % (curve["std"], FLAT_STD, curve["mean"] or 0, FLAT_MEAN),
                             orders))

    run = 1
    worst: list[int] = []
    for i in range(1, len(valid)):
        if valid[i]["tension_avg"] <= valid[i - 1]["tension_avg"]:
            run += 1
            if run >= RISE_WINDOW:
                worst = [m["order_no"] for m in valid[i - run + 1:i + 1]]
        else:
            run = 1
    if worst:
        out.append(_diag("R2", "mid", "连续多章无上升",
                         "第 %s 章连续走平/回落 → 缺推进感" % "-".join(str(o) for o in worst), worst))

    if len(valid) >= 2 and curve["peak_chapter"]:
        peak_no = curve["peak_chapter"]["order_no"]
        last = valid[-1]
        tail_start = orders[int(len(orders) * (1 - CLIMAX_TAIL))]
        if peak_no < tail_start or (last["tension_avg"] or 0) < (curve["peak_chapter"]["tension_avg"] or 0) * CLIMAX_RATIO:
            out.append(_diag("R3", "high", "高潮位置/收尾偏弱",
                             "峰值在第 %d 章（后 1/3 自第 %d 章起），末章 %.1f 为峰值的 %.0f%%"
                             % (peak_no, tail_start, last["tension_avg"] or 0,
                                100 * (last["tension_avg"] or 0) / max(1e-6, curve["peak_chapter"]["tension_avg"] or 1)),
                             [peak_no, last["order_no"]]))

    if len(valid) >= 3 and curve["peak_chapter"]:
        peak_no = curve["peak_chapter"]["order_no"]
        peak_val = curve["peak_chapter"]["tension_avg"] or 0
        after = [m for m in valid if m["order_no"] > peak_no]
        if after and not any((m["tension_avg"] or 0) <= peak_val * (1 - BREATH_DROP) for m in after):
            out.append(_diag("R4", "low", "峰值后无喘息",
                             "第 %d 章见峰（%.1f）后无任何章回落 ≥%.0f%% → 读者无缓冲位"
                             % (peak_no, peak_val, BREATH_DROP * 100),
                             [m["order_no"] for m in after]))

    if overdue:
        out.append(_diag("R5", "high", "伏笔逾期未回收",
                         "%d 条伏笔已过期望回收章仍未闭环" % len(overdue),
                         [f.get("text", "")[:30] for f in overdue[:5]]))

    open_fs = [f for f in foreshadows if f.get("status") in {"buried", "in_progress"}]
    closed_fs = [f for f in foreshadows if f.get("status") == "closed"]
    if len(open_fs) >= IMBALANCE_OPEN_MIN and len(open_fs) > max(1, len(closed_fs)) * IMBALANCE_RATIO:
        out.append(_diag("R6", "mid", "伏笔埋收失衡",
                         "未回收 %d 条 vs 已回收 %d 条 → 呼应会散" % (len(open_fs), len(closed_fs)),
                         [f.get("text", "")[:30] for f in open_fs[:5]]))

    if barren_scenes:
        out.append(_diag("R7", "low", "有正文无张力记录",
                         "%d 个场景已有正文但无有效张力（回退模式未评估）→ 曲线有缺口"
                         % len(barren_scenes), barren_scenes[:5]))
    return out


def build_foreshadow_network(foreshadows: list[dict],
                             scene_order: dict[str, int],
                             latest_order: Optional[int]) -> dict:
    """伏笔呼应网络：节点（埋设章/期望回收章/状态/逾期）+ 边（埋→收）。"""
    nodes: list[dict] = []
    edges: list[dict] = []
    overdue: list[dict] = []
    for f in foreshadows:
        buried = scene_order.get(str(f.get("buried_scene") or ""))
        expected = scene_order.get(str(f.get("expected_close_scene") or ""))
        status = str(f.get("status") or "buried")
        is_overdue = bool(expected is not None and latest_order is not None
                          and expected <= latest_order and status != "closed")
        node = {"id": f.get("id"), "type": f.get("type") or "plot",
                "text": str(f.get("text") or "")[:60], "status": status,
                "buried_chapter": buried, "expected_chapter": expected,
                "related_char_ids": f.get("related_char_ids") or [],
                "overdue": is_overdue}
        nodes.append(node)
        if buried is not None and expected is not None:
            edges.append({"from_chapter": buried, "to_chapter": expected,
                          "foreshadow_id": f.get("id")})
        if is_overdue:
            overdue.append(node)
    return {"nodes": nodes, "edges": edges, "overdue": overdue}


def build_global_view(chapters: list[dict], scenes: list[dict],
                      archives_by_scene: dict[str, list[dict]],
                      foreshadows: list[dict]) -> dict:
    """总入口：章级指标 + 曲线 + 诊断 + 伏笔网络 + 结构评分。"""
    metrics = chapter_metrics(chapters, scenes, archives_by_scene)
    curve = build_curve(metrics)

    # scene → 章序号（用于伏笔网络）
    ch_order = {str(c.get("id")): (c.get("order_no") or 0) for c in chapters}
    scene_order = {str(s.get("id")): ch_order.get(str(s.get("chapter_id") or ""), 0)
                   for s in scenes}
    done_orders = [m["order_no"] for m in metrics if m["prose_chars"] > 0]
    latest_order = max(done_orders) if done_orders else None
    fs = build_foreshadow_network(foreshadows, scene_order, latest_order)

    barren = [str(s.get("id")) for s in scenes
              if (s.get("final_prose") or "").strip()
              and not scene_tension(archives_by_scene.get(str(s.get("id"))))["has"]]

    diags = diagnose(metrics, curve, foreshadows, fs["overdue"], barren)
    score = max(0, 100 - sum(d["deduct"] for d in diags))
    return {
        "curve": metrics,
        "summary": dict(curve, chapters=len(metrics), done_chapters=len(done_orders),
                        foreshadows_open=len([f for f in foreshadows
                                              if f.get("status") in {"buried", "in_progress"}]),
                        foreshadows_closed=len([f for f in foreshadows
                                                if f.get("status") == "closed"])),
        "diagnostics": diags,
        "foreshadows": fs,
        "structure_score": score,
        "barren_scenes": barren,
    }
