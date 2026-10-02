"""ToolRegistry：工具声明的**单一事实来源**。

按钮与 agent 调**同一份实现**——一份声明、一套闸门、一套审计。
副作用等级：
  read         只读/只出报告，不改正文（可能落审计 note）
  write        只出候选（diff / 建议稿），作者采纳才写
  destructive  真正落库/落账/删改，**需作者确认**
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDecl:
    name: str
    desc: str
    side_effect: str                     # read | write | destructive
    params: tuple[str, ...] = ()          # 必需参数
    optional: tuple[str, ...] = ()
    needs_confirm: bool = False
    handler: str = ""                    # service.<name> 或 repo.<name>
    group: str = "prose"                 # 前端分组：prose / annotation / outline


TOOLS: dict[str, ToolDecl] = {
    # ---------- read ----------
    "prose.scan_tone": ToolDecl(
        "prose.scan_tone", "AI 味扫描（0-token 规则命中，改不了正文）", "read",
        params=("scene_id", "text"), handler="prose_ai_tone",
    ),
    "annotation.list": ToolDecl(
        "annotation.list", "列出本场批注（默认只看未处理）", "read",
        params=("scene_id",), optional=("status",), handler="repo.list_annotations", group="annotation",
    ),
    # ---------- write（候选，不落正文） ----------
    "prose.draft": ToolDecl(
        "prose.draft", "写手：生成本场正文初稿", "write",
        params=("scene_id",), handler="prose_draft",
    ),
    "prose.review": ToolDecl(
        "prose.review", "体检员：问题清单 + 口吻发现", "write",
        params=("scene_id", "text"), handler="prose_review",
    ),
    "prose.polish": ToolDecl(
        "prose.polish", "润色师：只改写法不改事实", "write",
        params=("scene_id", "text"), handler="prose_polish",
    ),
    "prose.verify": ToolDecl(
        "prose.verify", "质检员：伏笔/信念/因果/风险/状态变化", "write",
        params=("scene_id", "text"), handler="prose_verify",
    ),
    "prose.spot_fix": ToolDecl(
        "prose.spot_fix", "定点修复 AI 味命中句（含事实闸门+双闸复检）", "write",
        params=("scene_id", "text"), handler="prose_spot_fix",
    ),
    "prose.quality_loop": ToolDecl(
        "prose.quality_loop", "A2 质量回环：自评 → 定点/全文重写 → 复检", "write",
        params=("scene_id", "text"), handler="prose_quality_loop",
    ),
    # ---------- destructive（需确认） ----------
    "prose.save": ToolDecl(
        "prose.save", "保存正文到 scenes.final_prose（落库）", "destructive",
        params=("scene_id", "text"), needs_confirm=True, handler="save_scene_prose",
    ),
    "annotation.resolve": ToolDecl(
        "annotation.resolve", "把批注标记为已处理 / 撤销", "destructive",
        params=("annotation_id",), optional=("status", "handled_by_note_id"),
        needs_confirm=True, handler="repo.update_annotation", group="annotation",
    ),
}


def get_tool(name: str) -> ToolDecl | None:
    return TOOLS.get(name)


def list_tools() -> list[dict]:
    return [
        {
            "name": t.name, "desc": t.desc, "side_effect": t.side_effect,
            "params": list(t.params), "optional": list(t.optional),
            "needs_confirm": t.needs_confirm, "group": t.group,
        }
        for t in TOOLS.values()
    ]
