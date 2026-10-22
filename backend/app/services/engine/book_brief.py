"""全局脉络 + 骨架体检（v1.50 · 0-token，零 LLM）。

回答两个问题：
  ① 主笔"掌管全局"要看的**一页纸**（book.brief）：这本书现在什么形状——
     每章写了没 / 字数 / 走哪种生产方式 / 伏笔埋了几条收了几条 / 谁断档了。
  ② 骨架"好不好"（skeleton_check）：不靠 LLM 猜，用确定性规则把**能查的缺陷**列出来。

为什么用规则而不是问模型：
  - 这些缺陷（目标字数合计、伏笔只埋不收、角色断档、涌现章没指定推演场）都是**数据问题**，
    模型看了也只能说"可能有问题"；规则能指到"第 7 章 / 角色名 / 伏笔 id"。
  - 0-token = 每次排骨架前都能跑，不花钱、不慢、可复现。

口径纪律：字数一律走 text_metrics.chapter_chars（B39 单一口径），不在这里另算一遍。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.services.text_metrics import chapter_chars

MODE_LABEL = {"draft": "直写", "emerge": "涌现", "hybrid": "混合"}
MODES = tuple(MODE_LABEL)

CHAR_GAP_CHAPTERS = 3      # 连续几章没出场算"断档"
MIN_WRITTEN_FOR_FS_CHECK = 3   # 至少写了几章才可能"只埋不收"


def _production(chapter: dict) -> dict:
    """制作单（坏 JSON 一律当空，不让脏数据打断体检）。"""
    raw = chapter.get("production_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _status(chars: int) -> str:
    return "written" if chars > 0 else "planned"


def build_brief(*, book: dict, chapters: list[dict],
                scenes_by_chapter: dict[str, list[dict]],
                characters: list[dict], foreshadows: list[dict],
                world_states: list[dict], beliefs: list[dict]) -> dict:
    """装配全局脉络 + 骨架体检结果（纯函数：不读库、不调模型，便于单测）。"""
    rows: list[dict] = []
    scene_no: dict[str, int] = {}
    for ch in sorted(chapters, key=lambda c: int(c.get("order_no") or 0)):
        cid = str(ch.get("id") or "")
        no = int(ch.get("order_no") or 0)
        scenes = scenes_by_chapter.get(cid) or []
        chars = chapter_chars(ch, scenes)
        prod = _production(ch)
        for sc in scenes:
            scene_no[str(sc.get("id") or "")] = no
        rows.append({
            "no": no, "id": cid, "title": ch.get("title") or "",
            # label：给 UI/渲染用的完整章名（标题已自带「第 N 章」就不重复串）
            "label": (ch.get("title") or "").strip()
            if str(ch.get("title") or "").strip().startswith("第")
            else ("第 " + str(no) + " 章 " + str(ch.get("title") or "")).strip(),
            "tone": ch.get("tone") or "",
            "mode": ch.get("mode") or "draft",
            "mode_label": MODE_LABEL.get(ch.get("mode") or "draft", ch.get("mode") or "draft"),
            "word_target": int(ch.get("word_target") or 0),
            "chars": chars,
            "status": _status(chars),
            "scenes": len(scenes),
            "has_curve": bool(str(ch.get("tension_curve") or "").strip()),
            "hook": str(prod.get("hook") or ""),
            "must_rehearse": prod.get("must_rehearse") or [],
            "text": " ".join([str(ch.get("summary") or ""), str(ch.get("prose_md") or ""),
                              " ".join(str(s.get("title") or "") + str(s.get("goal") or "")
                                       for s in scenes)]),
        })

    written = [r for r in rows if r["status"] == "written"]
    total_words = sum(r["chars"] for r in rows)
    target_sum = sum(r["word_target"] for r in rows)

    # ---- 伏笔：三态 + 逾期 + 回收率 ----
    fs_counts = {"buried": 0, "in_progress": 0, "closed": 0}
    overdue: list[dict] = []
    max_written_no = max([r["no"] for r in written], default=0)
    for f in foreshadows:
        status = str(f.get("status") or "buried")
        fs_counts[status] = fs_counts.get(status, 0) + 1
        due_scene = str(f.get("expected_close_scene") or "")
        due_no = scene_no.get(due_scene, 0)
        if status != "closed" and due_no and max_written_no and due_no <= max_written_no:
            overdue.append({"id": f.get("id"), "text": str(f.get("text") or "")[:60],
                            "due_chapter": due_no, "status": status})
    fs_total = sum(fs_counts.values())
    fs_open = fs_counts["buried"] + fs_counts["in_progress"]
    recovery = (fs_counts["closed"] / fs_total) if fs_total else None

    # ---- 角色出场：按章正文/摘要/场文本里出现名字计 ----
    char_rows: list[dict] = []
    for c in characters:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        seen = [r["no"] for r in rows if name in r["text"]]
        worst_gap = 0
        for a, b in zip(seen, seen[1:]):
            worst_gap = max(worst_gap, b - a - 1)
        char_rows.append({"name": name, "appears_in": seen, "worst_gap": worst_gap})

    # ---- 体检：确定性规则 ----
    issues: list[dict] = []

    def add(level: str, code: str, msg: str, where: str = "") -> None:
        issues.append({"level": level, "code": code, "msg": msg, "where": where})

    for r in rows:
        if r["word_target"] <= 0:
            add("mid", "no_target", "没有字数目标（写手只能按默认 4000 字猜）", f"第 {r['no']} 章")
        if not r["has_curve"]:
            add("low", "no_curve", "没有张力曲线/节奏声明（写手不知道这段该升还是该落）", f"第 {r['no']} 章")
        if r["mode"] not in MODES:
            add("low", "bad_mode", f"生产方式取值非法：{r['mode']!r}（可选 {list(MODES)}）", f"第 {r['no']} 章")
        if r["mode"] == "emerge" and not r["must_rehearse"]:
            add("mid", "emerge_no_rehearse",
                "涌现章没指定必须推演的场（执行层不知道先演哪场）", f"第 {r['no']} 章")

    if fs_total and fs_open and fs_counts["closed"] == 0 and len(written) >= MIN_WRITTEN_FOR_FS_CHECK:
        add("high", "fs_no_close",
            f"伏笔只埋不收：{fs_total} 条里 closed = 0（写完 {len(written)} 章还没有一条回收）")
    for o in overdue:
        add("high", "fs_overdue",
            f"伏笔逾期未收：应于第 {o['due_chapter']} 章前回收，当前仍 {o['status']}——{o['text']}")

    written_nos = {r["no"] for r in written}
    for c in char_rows:
        if c["worst_gap"] >= CHAR_GAP_CHAPTERS:
            add("mid", "char_gap",
                f"角色断档：{c['name']} 连续 {c['worst_gap']} 章没出场（出场章：{c['appears_in']}）")
        elif not c["appears_in"] and len(written) >= MIN_WRITTEN_FOR_FS_CHECK:
            add("low", "char_orphan", f"角色从未出场：{c['name']}（已写 {len(written)} 章）")

    if rows and target_sum == 0:
        add("high", "no_target_at_all", "全书没有一章有字数目标——骨架等于没有量纲")

    high = len([i for i in issues if i["level"] == "high"])
    mid = len([i for i in issues if i["level"] == "mid"])
    low = len([i for i in issues if i["level"] == "low"])
    score = max(0, 100 - high * 20 - mid * 8 - low * 3)

    modes = {m: len([r for r in rows if r["mode"] == m]) for m in MODES}
    return {
        "book": {"id": book.get("id"), "title": book.get("title") or "",
                 "genre": book.get("genre") or "", "synopsis": book.get("synopsis") or ""},
        "chapters": [{k: v for k, v in r.items() if k != "text"} for r in rows],
        "foreshadows": {"total": fs_total, **fs_counts, "open": fs_open,
                        "recovery_rate": recovery, "overdue": overdue},
        "characters": {"total": len(char_rows),
                       "entries": sorted(char_rows, key=lambda c: -len(c["appears_in"]))},
        "facts": {"world_states": len(world_states), "beliefs": len(beliefs)},
        "metrics": {"chapters": len(rows), "written": len(written),
                    "planned": len(rows) - len(written), "total_words": total_words,
                    "target_sum": target_sum,
                    "target_progress": (total_words / target_sum) if target_sum else None,
                    "modes": modes},
        "issues": issues,
        "score": score,
        "counts": {"high": high, "mid": mid, "low": low},
    }


def render_brief(brief: dict[str, Any], *, max_chapters: int = 12,
                 budget: int = 1400) -> str:
    """把 brief 渲染成写进主笔 prompt 的一页纸（有预算：宁可省略，不许挤掉约束）。"""
    b = brief.get("book") or {}
    m = brief.get("metrics") or {}
    fs = brief.get("foreshadows") or {}
    lines: list[str] = []
    lines.append("【全局脉络（0-token 派生，非模型猜测）】")
    lines.append(f"书：{b.get('title') or '（未命名）'}｜{m.get('chapters', 0)} 章，已写 {m.get('written', 0)} 章"
                 f"｜{m.get('total_words', 0)} 字 / 目标 {m.get('target_sum', 0)} 字")
    modes = m.get("modes") or {}
    if any(modes.values()):
        lines.append("生产方式：" + " · ".join(f"{MODE_LABEL.get(k, k)} {v} 章"
                                            for k, v in modes.items() if v))
    rows = brief.get("chapters") or []
    if rows:
        lines.append("章节：")
        for r in rows[:max_chapters]:
            seg = f"- {r['label']}｜{r['mode_label']}｜目标{r['word_target']}字"
            seg += f"｜已写{r['chars']}字" if r["chars"] else "｜未写"
            if not r.get("has_curve"):
                seg += "｜无节奏声明"
            lines.append(seg)
        if len(rows) > max_chapters:
            lines.append(f"- …另有 {len(rows) - max_chapters} 章（见全局脉络页）")
    if fs.get("total"):
        rate = fs.get("recovery_rate")
        lines.append(f"伏笔：共 {fs['total']} 条（埋着 {fs.get('buried', 0)} / 进行中 {fs.get('in_progress', 0)}"
                     f" / 已收 {fs.get('closed', 0)}，回收率 "
                     f"{'—' if rate is None else f'{rate:.0%}'}）")
    entries = (brief.get("characters") or {}).get("entries") or []
    if entries:
        top = "；".join(f"{c['name']}({len(c['appears_in'])} 章)" for c in entries[:6])
        lines.append("角色出场：" + top)
    issues = brief.get("issues") or []
    if issues:
        lines.append(f"骨架体检：{brief.get('score')} 分，{len(issues)} 条待处理")
        for i in issues[:6]:
            lvl = {"high": "高危", "mid": "中等", "low": "轻微"}.get(i["level"], i["level"])
            where = f"（{i['where']}）" if i.get("where") else ""
            lines.append(f"- [{lvl}] {i['msg']}{where}")
    out = "\n".join(lines)
    return out if len(out) <= budget else out[: budget - 1] + "…"


def skeleton_check(brief: dict[str, Any]) -> dict[str, Any]:
    """骨架体检的**裁剪视图**（工具/接口给主笔与作者看的就这一份，不重复算）。"""
    return {"score": brief.get("score"), "issues": brief.get("issues") or [],
            "counts": brief.get("counts") or {}, "metrics": brief.get("metrics") or {}}
