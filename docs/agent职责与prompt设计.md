# Agent 职责与 Prompt 设计稿

> 版本 v1 · 2026-08-25 · 依赖：`docs/MVP设计.md`（北极星/铁律）、`docs/prompt核心设定.md`（四层 prompt 分层）
> 定位：S2（主笔）/S3（收束分析）/S4（成文升级）的 **prompt 与 skill 设计源头**。实现时以本文档为准，运行时 prompt 内容以 `.trae/skills/<name>/SKILL.md` 与引擎代码内嵌默认值为准。

---

## 1. 四席职责总表（不越界）

| 席 | 别名 | 管什么 | 不管什么（铁律） | 模型路由 | 现实现状 |
|---|---|---|---|---|---|
| **主笔** | 总编剧 | 书/章/场景结构：世界观、章节骨架（基调/张力曲线/字数）、场景清单、结局集合、伏笔埋设计划 | 不写任何现场台词；不参与回合循环 | `model_cheap`（flash） | 不存在（S2 新建） |
| **导演** | 执行导演 | 这场戏怎么演：张力/行动顺序/事件注入/信息曝光/举手/收束判定；场景收束时的**长程汇报** | 只给方向不给台词（铁律#1）；不替角色决策 | `model_cheap`（flash） | `engine/director.py` 已具备核心；缺收束分析（S3） |
| **角色** | 演员 | 在自己的感知子集+信念账本里涌现言行（黑盒） | 角色决策不可被外部改写 | `model_strong`（pro） | `engine/character.py` 已具备（think/decide 走 call_strong） |
| **成文** | 渲染器 | 事件流 → 小说正文（旁白/环境/动作/心理/打斗） | 不改变剧情事实，只决定怎么写 | `model_strong`（pro） | 极简渲染（`_default_prose`/`stream_prose`），S4 升级 |

**四层 prompt 分层对齐**（来自 `docs/prompt核心设定.md`，只做引用不重写）：
- ①书层（顶层共享）：世界观/人物关系/全书规矩 → 注入主笔、导演收束分析、编辑；不塞角色卡
- ②导演层（全局）→ 主笔=②+**结构生成**；导演=②+**回合调度**+**收束汇报**
- ③环境感知层（场景动态）→ 角色感知输入（现有 `perceive_context`）
- ④角色卡层（私有每角色）→ 角色演绎（现有 `_compose_prompt`）

---

## 2. 主笔（S2 新建 · `engine/chief_planner.py`）

**输入**（结构化）：
- 一句话方向（作者）＋ 可选参考/灵感
- （已有书时）现状：`worldview_json` 已有内容、已完成章节摘要、伏笔账本现状

**输出**（`model_cheap` 结构化 JSON，预览不落库 → commit 落库）：
```json
{
  "worldview": { "premise": "一句话主线", "rules_text": "## 规则 段（自然语言）", "background": "世界背景" },
  "chapters": [
    {
      "title": "第一章 秘境夺宝", "tone": "action", "tension_curve": "raise/hold/release",
      "word_target": 2500, "summary": "本章要完成的事",
      "scenes": [
        { "title": "秘境入口", "stage_desc": "峭壁前的禁制光幕", "cast": ["chenmo","liwen"], "initial_facts": ["..."], "goal": "这场戏的张力目标/结局条件" }
      ]
    }
  ],
  "foreshadow_plan": [ { "text": "……", "type": "plot", "buried_scene": "scene-1-2", "expected_close_scene": "scene-2-1" } ],
  "ending_options": ["结局A", "结局B"]
}
```

**system prompt 骨架**（写进 `主笔规划` skill）：
```
你是小说【主笔】。只负责结构，绝不写现场台词。
把作者一句话方向扩成可执行的书籍骨架：世界观（含可校验规则清单）、章节清单（每章基调/张力曲线/字数目标）、
每章的场景清单（舞台/在场角色/本场目标）、伏笔埋设与期望回收、全局结局集合。
规则一律以自然语言写进 ## 规则 段（后续由结构化解析器转 WorldRule）。
铁律：角色行为、对白、情绪都留给角色与导演涌现；你只定"这件事这本书要发生什么、按什么顺序"。
阅读 docs/prompt核心设定.md 的四层分层，只读①书层数据。
```

**落库行为**（commit）：
- books + `worldview_json` + `world_rules_json`（LLM 解析规则，见 §3.3）
- chapters（tone/tension_curve/word_target）+ scenes（stage_desc/cast/initial_facts）
- foreshadows 表（foreshadow_plan → buried 状态）

---

## 3. 导演（已有 + S3 增强）

### 3.1 回合模式（现状，不动）
`director.plan()` 已产出：曝光/调权/事件注入/stage_prompt/举手；`llm_converge` 已含 6 回合护栏+连续两票。挂 `导演调度` skill 内部规则。**不做结构性改动，只把 `导演调度` skill 的规则抄正**。

### 3.2 长程汇报模式（S3 新增 · `analyze_scene_close`）
场景收束（converged）后触发一次，用 `model_cheap`，**只读**，绝不打断回合内涌现。

**输入**：本场事件流（sim.events）、张力历史（turn_archives）、信念变化（beliefs）、伏笔现状（foreshadows 表）、剩余场景清单。
**输出**：
```json
{
  "scene_summary": "本场发生什么（≤150字，存 scene 供跨场记忆/时间线）",
  "foreshadow_updates": [ { "id": "F001", "status": "in_progress|closed", "reason": "…" } ],
  "character_arc_deltas": { "chenmo": "从隐忍转向决断" },
  "causality": [ { "event_id": "E-012", "caused_by": "E-008", "causal_pressure": 0.8 } ],
  "next_scene_hint": "下一场建议：…（驱动换场；可含插入/跳过提议）"
}
```
挂 `张力场判定` skill 做节奏评估。"生成与批评分离"：汇报中的质量/节奏判断走独立评估口径，不导演自吹（MVP §6.2）。

### 3.3 世界观规则解析（S2 做 · 解析一次检测免费）
`worldview_json.rules_text` → LLM（flash）转结构化：
```json
[
  { "concept": "筑基境", "constraint": "不能动用空间挪移", "rule_type": "negation",
    "keywords": ["筑基", "挪移", "空间跳跃"], "constraint_keywords": ["不能", "无法"] }
]
```
落 `world_rules_json`。**运行时检测 = 正则关键词匹配（0 token）**，见 `docs/agent职责与prompt设计.md` §6 / S4.1。

---

## 4. 角色（已有 · 现状对齐，不改结构）

- 感知：`perceive_context(char_id)` 按 `visible_to` 裁剪 → ③环境感知层
- 思考：`stream_think`（并行、逐 token、call_strong）→ 短思考（已收短）
- 决策：`decide`（串行、近况感知、call_strong）→ 行动 JSON
- 护栏：`persona_guard`（底线关键词）+ `world.check_fact_consistent`（结构查重）+ S4 加**规则校验**
- prompt 组装 `_compose_prompt(card, context, stage)`：作者腔调 → static_world（世界观）→ 感知现场 → 要求

挂 `角色演绎` skill、`角色卡生成器` skill（生成五维卡+Soul Field，长 role 档案建 `CharacterCard`）。

---

## 5. 成文（S4 升级 · 从极简到旁白体）

**现状**：`_default_prose`（确定性拼接）+ `stream_prose`（LLM 但极简）。
**升级**：接入 `骨架→正文` skill（含反 AI 味清单，story-craft 技法移植：
张力分层、环境景物描写、动作留白、心理细节、对话语气分层）。

**system prompt 骨架**：
```
你是小说【成文者】。输入：本回合事件流（角色行动/对白/导演注入/环境变化）。
任务：渲染成小说正文段落——补旁白（环境/景物/天气）、动作描写、心理细节；对白保留原意但润色。
铁律：不得改变事件流里的事实与角色原意；不得让角色说出事件之外的话；
玄幻打斗用招式/灵力消耗/攻防距离呈现，不是"他打了她一拳"式的干话。
产出普通话术：叙述是叙述、对话是对话，不用剧本标签。
```

---

## 6. 参考借鉴落地点（学自 STORYWRITER / NovelForge / OpenNovel）

| 借鉴 | 落地点 | 进入哪一席 |
|---|---|---|
| **结构化大纲/事件图**（STORYWRITER） | chapter/scene 落库 = 简版事件图；写场时只注入相关 facts 子集 | 主笔（骨架） + 角色（感知裁剪） |
| **生成-批评分离**（NovelForge Writer⇄Editor） | `张力场判定` skill 独立评估；`analyze_scene_close` 的质量判断不自评 | 导演（长程汇报） |
| **Canon 规则校验**（OpenNovel，纯正则 0 token） | `WorldRule[]` + 护栏第 4 步；成文 suggestion 告警 | 护栏/成文（S4.1） |
| **伏笔三态生命周期**（OpenNovel Director） | `foreshadows` 表 buried/in_progress/closed + `expected_close_scene` | 主笔（埋设计划）+ 导演（收束推进/回收） |
| **事件因果链**（OpenNovel CausalGraph） | `Event.caused_by/causal_pressure` 收束批量补；伏笔追踪地基 | 导演（收束分析） |
| **分层记忆压缩**（NovelForge 三层） | facts（语义）+ beliefs（谁知道什么）+ 场景摘要（收束产出） | 全席（共享状态） |
| **不抄**：按大纲直写 + 评分重写循环 | 与"角色黑盒涌现"铁律冲突 | — |

---

## 7. skill 映射（S0.2 用 skill-creator 创建，MVP §12.2）

| skill | 归属席 | 核心内容来源 |
|---|---|---|
| `主笔规划` | 主笔 | §2 prompt 骨架 + 骨架 JSON 格式 |
| `导演调度` | 导演（回合） | 三把巧劲 + 举手机制 + 收束判定（MVP §6） |
| `张力场判定` | 导演（评估） | 独立张力/divergence 评估（不自评） |
| `角色卡生成器` | 角色 | 五维卡 + Soul Field（story-craft 移植） |
| `角色演绎` | 角色 | 感知→决策→行动 + 四层注入（prompt核心设定.md） |
| `骨架→正文` | 成文 | §5 prompt 骨架 + 反 AI 味清单（story-craft 移植） |