"""章级生产方式 → 执行路径裁决（v1.52 · 0-token）。

直接回答一个产品问题：**这一章到底该怎么生产？**
  draft  直写：写手按字数目标一次写满（不需要推演）
  emerge 涌现：必须先把指定场（没指定=全部场）推演完，再据推演成果渲染成章
  hybrid 混合：只有关键场（must_rehearse）要先推演，其余直写

为什么把这条判断做成"确定性函数 + 页面照它走"：
  原来「去 /write 还是去 /director」是作者记性的事，走错了才发现顺序反了；
  现在 mode 是主笔裁决的数据，执行层据此选路，作者不需要记路由。

边界（诚实）：这里只做**裁决与引导**——真正跑推演仍是导演台的活（一次推演几十回合、
要选角、要 LLM），不在本函数里偷偷起任务。函数只回答"还差什么、下一步去哪"。
"""
from __future__ import annotations

import json

MODE_LABEL = {"draft": "直写", "emerge": "涌现", "hybrid": "混合"}
MODES = tuple(MODE_LABEL)


def _production(chapter: dict) -> dict:
    raw = chapter.get("production_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def required_scenes(mode: str, must: list[str], scene_ids: list[str]) -> list[str]:
    """必须先推演的场：emerge 没指定就全都要；hybrid 只看关键场；draft 不需要。"""
    if mode == "emerge":
        return list(must or scene_ids)
    if mode == "hybrid":
        return list(must)
    return []


def plan_production(*, chapter: dict, scenes: list[dict],
                    turns_by_scene: dict[str, int] | None = None) -> dict:
    """裁决本章的生产路径。纯函数：不读库、不起任务，便于单测与前端直接展示。"""
    turns = turns_by_scene or {}
    raw_mode = str(chapter.get("mode") or "draft")
    mode = raw_mode if raw_mode in MODES else "draft"
    prod = _production(chapter)
    must = [str(x) for x in (prod.get("must_rehearse") or []) if str(x or "").strip()]
    scene_ids = [str(s.get("id") or "") for s in (scenes or []) if s.get("id")]

    required = required_scenes(mode, must, scene_ids)
    pending = [sid for sid in required if int(turns.get(sid, 0) or 0) <= 0]
    rows: list[dict] = []
    for s in scenes or []:
        sid = str(s.get("id") or "")
        rows.append({
            "id": sid, "title": str(s.get("title") or ""),
            "turns": int(turns.get(sid, 0) or 0),
            "finalized": bool(str(s.get("final_prose") or "").strip()),
            "required": sid in required,
        })

    notes: list[str] = []
    if raw_mode not in MODES:
        notes.append("生产方式取值非法（" + raw_mode + "），按直写处理")
    if mode == "emerge" and not must:
        notes.append("涌现章未指定 must_rehearse，已按「全部场都要先推演」处理")
    if mode == "hybrid" and not must:
        notes.append("混合模式未指定关键场，实际等同直写")

    if mode == "draft":
        action, can_write = "draft", True
        why = "直写：写手按字数目标一次写满，不需要先推演"
    elif not scene_ids:
        action, can_write = "manual", False
        why = "涌现/混合模式需要有场：本章还没拆场，先去主笔拆场或改成直写"
    elif pending:
        action, can_write = "rehearse", False
        why = "还差 " + str(len(pending)) + " 场推演：先把它们演完，写手才有据可渲染"
    else:
        action, can_write = "draft", True
        why = ("所需推演已完成，可据推演成果渲染" if mode != "draft" else "可以直写")

    return {
        "mode": mode, "mode_label": MODE_LABEL[mode],
        "production": prod,
        "hook": str(prod.get("hook") or ""),
        "scenes": rows,
        "required": required,
        "pending": pending,
        "next_action": action,
        "can_write": can_write,
        "why": why,
        "notes": notes,
        "turns_total": sum(int(turns.get(sid, 0) or 0) for sid in scene_ids),
    }


def rehearsal_block(*, scenes: list[dict], turns_by_scene: dict[str, int] | None = None,
                    tail: int = 220) -> str:
    """把「已推演的场」压成写手 prompt 里的一块：据成果渲染，别重演、别改设定。"""
    turns = turns_by_scene or {}
    lines: list[str] = []
    for i, s in enumerate(scenes or [], start=1):
        sid = str(s.get("id") or "")
        n = int(turns.get(sid, 0) or 0)
        summary = " ".join(str(s.get("scene_summary") or "").split())
        body = " ".join(str(s.get("final_prose") or "").split())
        if not n and not summary and not body:
            continue
        label = str(s.get("title") or ("第 " + str(i) + " 场"))
        head = "- " + label + "："
        if n:
            head += "已推演 " + str(n) + " 回合｜"
        if summary:
            head += "场摘要：" + summary
        elif body:
            head += "已定稿正文节选：" + body[-tail:]
        else:
            head += "（推演过但无摘要，按场目标渲染即可）"
        lines.append(head)
    if not lines:
        return ""
    return ("【已推演的场（据成果渲染：不要重演过程、不要改设定，只需把它写成正文）】\n"
            + "\n".join(lines))
