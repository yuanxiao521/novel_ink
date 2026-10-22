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
    writes: str = ""                     # 写目标（permissions.FIELD_OWNERS 的键；"" = 不落库/只出候选）


TOOLS: dict[str, ToolDecl] = {
    # ---------- read ----------
    "prose.scan_tone": ToolDecl(
        "prose.scan_tone", "AI 味扫描（0-token 规则命中，改不了正文）", "read",
        params=("scene_id", "text"), handler="prose_ai_tone",
    ),
    "prose.quality_score": ToolDecl(
        "prose.quality_score", "评审：五维自评 + 0-token 硬信号合分（只读，不改正文）", "read",
        params=("scene_id",), optional=("text",), handler="prose_quality_score",
    ),
    "prose.review_meeting": ToolDecl(
        "prose.review_meeting", "审稿会：体检 → 润色（有问题才做）→ 质检 → 评审，一次跑完并给裁决", "write",
        params=("scene_id",), optional=("text",), handler="prose_review_meeting",
    ),
    "character.voices": ToolDecl(
        "character.voices", "角色在场感：请本场角色就当前正文说一句（只读，不改正文）", "read",
        params=("scene_id",), optional=("text",), handler="character_voices", group="character",
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
        writes="scenes:final_prose",
    ),
    "annotation.resolve": ToolDecl(
        "annotation.resolve", "把批注标记为已处理 / 撤销", "destructive",
        params=("annotation_id",), optional=("status", "handled_by_note_id"),
        needs_confirm=True, handler="repo.update_annotation", group="annotation",
        writes="prose_annotations",
    ),
}


TOOLS.update({
    # ---------- 任务总线（L3）：请求 / 查看 / 流转 ----------
    "task.rehearsal": ToolDecl(
        "task.rehearsal", "请求本场先彩排一次（派给场记/角色，产物回填本场）", "write",
        params=("scene_id",), optional=("goal", "kind", "to_agent", "from_agent"),
        handler="agent_task_create", group="task",
        writes="agent_tasks",
    ),
    "task.list": ToolDecl(
        "task.list", "列出本场任务单（含状态）", "read",
        params=("scene_id",), handler="agent_task_list", group="task",
    ),
    "task.transition": ToolDecl(
        "task.transition", "推进任务状态（working / input-required / completed / failed）", "destructive",
        params=("task_id", "status"), optional=("note",), needs_confirm=True,
        handler="agent_task_transition", group="task",
        writes="agent_tasks",
    ),
})

TOOLS.update({
    # ---------- 主笔（book 级 · v1.40 治理补齐）----------
    # 此前主笔的三个写操作走"散装 API"（不经 ToolExecutor）：不过确认闸门、不落审计、
    # AgentSpec 声明了工具名但 registry 里根本没有 → 对话无法驱动。现在补齐。
    "outline.plan": ToolDecl(
        "outline.plan", "主笔排一批章节骨架预览（A4 分批，batch_size 默认 3；不落库，预览后由 outline.commit 落库）", "write",
        params=("book_id",), optional=("direction", "batch_size", "batch_from"),
        handler="plan_book", group="chief",
    ),
    "outline.commit": ToolDecl(
        "outline.commit", "把骨架预览落库（默认追加模式；替换已有正文需 allow_overwrite）", "destructive",
        params=("book_id", "plan"), optional=("mode", "allow_overwrite"), needs_confirm=True,
        handler="commit_book_plan", group="chief",
        writes="chapters",
    ),
    "characters.generate": ToolDecl(
        "characters.generate", "让主笔按书名/方向生成 3-5 张角色卡（腔调可辨）", "write",
        params=("book_id",), optional=("direction",),
        handler="generate_book_characters", group="chief",
        writes="characters",
    ),
    "memory.write": ToolDecl(
        "memory.write", "写一条书级记忆（topic=direction/setting/constraint/history/preference）", "destructive",
        params=("book_id", "topic", "content"), needs_confirm=True,
        handler="create_memory", group="chief",
        writes="book_memories",
    ),
})


TOOLS.update({
    # ---------- 全局脉络 / 章级制作单（v1.50 · 主笔"掌管全局"的两只手）----------
    # book.brief：0-token 派生的一页纸（章状态/字数/伏笔回收/角色断档/缺制作单的章），
    # 主笔不用"凭印象"排骨架；页面与对话共用同一份，口径不分叉。
    "book.brief": ToolDecl(
        "book.brief",
        "读全局脉络 + 骨架体检（0-token）：每章状态/字数/伏笔回收率/角色断档/缺制作单的章",
        "read",
        params=("book_id",), handler="get_book_brief", group="chief",
    ),
    # chapter.production：结构决策（这一章走直写/涌现/混合）——主笔写结构、不写正文。
    "chapter.production": ToolDecl(
        "chapter.production",
        "写章级制作单：mode=draft/emerge/hybrid + 必须推演的场 + 钩子（只改结构，不碰正文）",
        "write",
        params=("book_id", "chapter_no"),
        optional=("mode", "production", "chapter_id"),
        handler="set_chapter_mode", group="chief", writes="chapters:production",
    ),
})


TOOLS.update({
    # ---------- 记忆检索（v1.49 · RAG）----------
    # 与写作页「记忆」面板同一份检索实现（rag.recall），所以对话里问到的
    # 「前情/这个角色/世界观/有哪些没收的伏笔」和写手 prompt 看到的是同一批素材，
    # 不会出现"对话说一套、落笔写一套"。
    "memory.search": ToolDecl(
        "memory.search", "记忆检索：按查询取前情/相关角色/世界观/未回收伏笔片段（只读）", "read",
        params=("book_id", "query"), optional=("kinds", "top_k"),
        handler="memory_search", group="memory",
    ),
})



TOOLS.update({
    # ---------- 章级审校（v1.65 · 两个页面合并：责编在章级要能真的动手）----------
    # 与场级 prose.* 一一对应，差别只有两点：单位是**章正文**（chapters.prose_md，唯一真源）、
    # 审计落 prose_notes.chapter_id。参数不再要 scene_id —— 章级不依赖拆场。
    # v1.67：主笔的「只读眼睛」——看正文但不改字（路线图 P3.7-B）
    "prose.audit": ToolDecl(
        "prose.audit",
        "骨架遵循度审计（只读）：按 chapter_no（如「第 3 章」）或 chapter_id 审一章，"
        "对照章目标/张力曲线/卷目标输出偏离项与建议，不改一个字",
        "read",
        params=(),                                  # 章号与 chapter_id 二选一（跨参数校验在 service）
        optional=("chapter_id", "chapter_no", "book_id", "text"),
        handler="chapter_prose_audit", group="chief",
    ),
    # v1.69（方案②）：按一条审校建议产候选（**不落库**，作者采纳才写正文并存版本）
    "chapter.rewrite": ToolDecl(
        "chapter.rewrite", "按一条审校建议改稿：产出候选（不落库），作者「采纳并保存」后才写正文并存版本",
        "write", params=("chapter_id",),
        optional=("instruction", "scope", "para_index", "scene_id", "text", "chapter_no", "book_id"),
        handler="chapter_rewrite", group="chapter",
    ),
    "chapter.review": ToolDecl(
        "chapter.review", "体检整章：问题清单 + 口吻发现（章级）", "write",
        params=("chapter_id",), optional=("text",), handler="chapter_prose_review", group="chapter",
    ),
    "chapter.verify": ToolDecl(
        "chapter.verify", "质检整章：伏笔/信念/因果/状态变化/风险（章级）", "write",
        params=("chapter_id",), optional=("text",), handler="chapter_prose_verify", group="chapter",
    ),
    "chapter.polish": ToolDecl(
        "chapter.polish", "润色整章：只改写法不改事实（产出候选，作者采纳才落库）", "write",
        params=("chapter_id",), optional=("text",), handler="chapter_prose_polish", group="chapter",
    ),
    "chapter.scan_tone": ToolDecl(
        "chapter.scan_tone", "AI 味扫描整章（0-token 规则命中，不花模型）", "read",
        params=("chapter_id",), optional=("text",), handler="chapter_prose_scan_tone", group="chapter",
    ),
    "chapter.quality_score": ToolDecl(
        "chapter.quality_score", "评审整章：五维自评 + 0-token 硬信号合分（只读）", "read",
        params=("chapter_id",), optional=("text",), handler="chapter_prose_quality", group="chapter",
    ),
    "chapter.inspect": ToolDecl(
        "chapter.inspect", "章后巡检：体检 ∥ 质检 → 评分 → 0-token 裁决 → 自动记账（后台任务）", "read",
        params=("chapter_id",), handler="chapter_inspect", group="chapter",
    ),
    "chapter.context": ToolDecl(
        "chapter.context", "看本章的写作上下文（前情 + 记忆五段 + 场清单），只读", "read",
        params=("chapter_id",), handler="chapter_prose_context", group="chapter",
    ),
    "chapter.merge_scenes": ToolDecl(
        "chapter.merge_scenes", "把已推演的场正文并入章正文（章正文是唯一真源；幂等，已并入的跳过）", "destructive",
        params=("chapter_id",), optional=("scene_id",), needs_confirm=True,
        handler="merge_scene_into_chapter", group="chapter", writes="chapters:prose",
    ),
})

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
