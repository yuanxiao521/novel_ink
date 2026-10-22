"""记忆块 kind 常量（v1.62）：索引器 / 存储 / 召回三处共用，避免同一个字符串各写一份。

为什么不放 indexer 里：store 的「章号时间闸」需要按 kind 判断哪些素材受时间序约束，
而 indexer 又 import store（写库）。抽到独立模块既避免循环 import，也让
"新增一个 kind 要同时改哪几处"变成一处可查的清单。
"""
from __future__ import annotations

# ---- 章级（受 chapter_no 时间轴约束：只能看到当前章之前的） ----
CHAPTER_KIND = "chapter"                    # 兜底：摘要 + 正文尾（没有 digest 的章）
DIGEST_FACT_KIND = "chapter_fact"           # 事实面：事件 / 状态迁移 / 新确立设定
DIGEST_KNOWLEDGE_KIND = "chapter_knowledge"  # 知晓面：谁在这一章知道了什么
DIGEST_HOOK_KIND = "chapter_hook"           # 悬念面：章末钩子 + 未解问题
VOLUME_KIND = "volume"                      # 卷级 digest（已写完的卷）

# ---- 全局（不随时间过滤） ----
SCENE_KIND = "scene"
CHARACTER_KIND = "character"
WORLD_KIND = "world"
FORESHADOW_KIND = "foreshadow"
FORESHADOW_TRACE_KIND = "foreshadow_trace"   # 伏笔在哪一章被推进（来自章 digest）
BELIEF_KIND = "belief"
MEMORY_KIND = "memory"

# 受 chapter_no 时间闸约束的 kind：检索 chapter_no_lt 时只取「当前章之前」的
CHAPTER_SCOPED_KINDS: tuple[str, ...] = (
    CHAPTER_KIND, DIGEST_FACT_KIND, DIGEST_KNOWLEDGE_KIND, DIGEST_HOOK_KIND, VOLUME_KIND,
)

# 给前端/统计用的人类可读名（mem_chunks.kind 是机器名，别直接暴露给作者）
KIND_LABEL: dict[str, str] = {
    CHAPTER_KIND: "章摘要",
    DIGEST_FACT_KIND: "章事实",
    DIGEST_KNOWLEDGE_KIND: "章知晓",
    DIGEST_HOOK_KIND: "章钩子",
    VOLUME_KIND: "卷概要",
    SCENE_KIND: "场",
    CHARACTER_KIND: "角色",
    WORLD_KIND: "世界观",
    FORESHADOW_KIND: "伏笔",
    FORESHADOW_TRACE_KIND: "伏笔推进",
    BELIEF_KIND: "信念",
    MEMORY_KIND: "书级记忆",
}
