# 多 Agent 创作系统（主笔/导演/角色/成文）落地计划 v2

> 目标：把系统从"单场景推演 demo"升级为**能完整写出一本玄幻小说的多 Agent 创作闭环**——主笔定结构、导演管回合、角色涌现、成文渲染，作者在各层共创。
> 决策汇总（用户逐轮拍板）：
> 1. **一场景一 sim**，场景切换=开新 sim，共享世界状态（搬世界不搬现场）
> 2. **换场由导演自动判定**（收束后查场景清单续拍下一场）
> 3. **引入独立"主笔 Agent"**：书/章/场景结构由主笔产出，导演只管回合调度，职责彻底分清
> 4. **"从零开始一本书"走双入口**：手动排结构 / 让主笔规划（后者为主路径，骨架**预览确认后落库**）
> 5. **参考业界多 Agent 小说系统**（STORYWRITER / NovelForge / OpenNovel 等）优化职责划分与 prompt/skill 设计
> 6. **story-craft（TRAE 官方插件）作为写作技法内核**：五维卡/张力分层/反 AI 味/契约造戏剧 → 移植进本项目 skill 内部规则；运行时**不依赖**插件
> 7. **OpenNovel 四条例接（源码级学习后吸收）**：世界观规则校验器（玄幻硬约束）、伏笔账本（三态生命周期）、事件因果字段（caused_by/causal_pressure）、场景收束全局分析（导演长程模式）——详见 §2.1
> 8. **三个深入拍板**：世界观规则用 **LLM 解析**（自然语言→结构化 WorldRule，检测阶段正则 0 token）；因果字段**收束时批量补**；伏笔检测**收束时触发**

---

## 1. 开发现状对齐

### 1.1 已经建好（本次探索确认）

| 层 | 现状 | 文件 |
|---|---|---|
| **DB 表** | Book/Chapter/Scene/Character/Simulation 五张 ORM 表全存在，Simulation 含三级外键（均可 null） | `backend/app/db/models/*.py` |
| **迁移** | `0001_init_schema` 一次性建全部表，`upgrade head` 幂等 | `backend/alembic/versions/0001_init_schema.py` |
| **Repo CRUD** | save/load/delete sim（整份 state_json 快照）；四层 list/save/get；`resume_latest(scene_id)`；`get_book_tree` 聚合树 | `backend/app/db/repo.py` |
| **种子数据** | `book-rain → chapter-01 → scene-betrayal-night`（3 角色卡）幂等灌库 | `backend/app/db/seed.py` |
| **路由（读）** | `/books`、`/books/{id}/chapters`、`/books/{id}/tree`、`/chapters/{id}/scenes`、`/scenes/{id}`（含角色） | `backend/app/api/routers/simulation.py` |
| **服务 start** | 按 scene 四层装配：scene → scenario spec → facts → plan_cfg → 角色卡 → sim 快照；book/chapter 缺省反查；resume 走 resume_latest | `backend/app/services/service.py` |
| **sim 生命周期** | start/step/history/rewind/pause/resume/state/stream(SSE)/intervene/director-chat 全通 | 同上 |
| **导演引擎** | 每回合 plan（曝光/调权/事件注入 → 真落世界）+ 张力测量 + 举手机制 + 收束判定（6 回合护栏+连续两票）+ 共创 chat | `backend/app/services/engine/director.py` / `graph.py` |
| **角色引擎** | 感知子集 → 思考(并行流式) → 决策(串行近况感知) → 护栏(人设/世界事实+熔断) | `backend/app/services/engine/character.py` |
| **前端 API** | listBooks / fetchBookTree / listChapters / listScenes / createOrResumeSim | `frontend/src/api/novel.ts` |
| **前端导演台** | DirectorPage 已按 sceneId 启动；事件级 SSE 流（感知/思考增量/行动/导演/成文 token）；时间线回退查看；共创对话台 | `frontend/src/hooks/useDirectorSim.ts` 等 |

### 1.2 缺口清单（本计划要做的事）

| # | 缺口 | 影响 |
|---|---|---|
| G1 | 后端无**写接口**（路由只有 GET，repo 的 save_* 未暴露） | 无法建书/排章/建场景/改角色 |
| G2 | 前端无**规划 UI**（Dashboard 是静态 mock） | 作者无法管理书的结构 |
| G3 | DirectorPage 顶部**硬编码**（背叛之夜/场景：书房夜谈） | 切书/切场景无入口 |
| G4 | 无**章/场景导航**选择器 | 无法从书架进指定场景 |
| G5 | **换场机制**不存在（收束 → 自动开下一 scene sim → 前端切换） | "只跑一场就完"体验根源 |
| G6 | **主笔 Agent 不存在**（无书级结构生成：世界观/章/场景清单/骨架） | 从零开始只能手排 |
| G7 | **skill 体系未建**（MVP §12 首批 5 骨架仅登记未落地） | 导演/角色/成文的专业技艺没有固化 |
| G8 | **成文层是最简渲染**（事件流直出，无旁白/环境/心理描写） | 输出"剧本感"而非"小说感"；玄幻打斗/旁白无法体现 |
| G9 | **世界观无硬约束载体**（玄幻境界/规则应进 hard facts 由护栏管） | 跑玄幻必然出现"筑基用挪移"类硬伤 |
| G10 | **共创对话台未成决策台**（有 chat，但无"主笔/导演给方案 A/B → 作者拍板"形态） | 共创缺落点 |

---

## 2. 参考基准（S0 调研对象，已确认可参考）

| 项目 | 值得学的 | 对应到本系统 |
|---|---|---|
| **STORYWRITER**（清华，arXiv:2506.16445，[github.com/THU-KEG/StoryWriter](https://github.com/THU-KEG/StoryWriter)） | 三模块：outline agent（事件图大纲）/ planning agent（章节内事件分配）/ writing agent（按需压缩历史）——与"主笔/导演/写手"高度同构；**事件关系图**保持跨章一致性 | outline=主笔，planning=导演，writing=成文 |
| **NovelForge**（[github.com/xiehuanyi/NovelForge](https://github.com/xiehuanyi/NovelForge)） | 6 Agent 流水线（WorldBuilder→Character→Outliner→Writer⇄Editor）；**Writer-Editor 多轮互评**；**3 层记忆**（工作/情景/语义）；写了一本 400 章 108 万字 | 分批学习：3 层记忆=我们的 facts/beliefs/记忆；互评=成文+一致性审校 |
| **OpenNovel**（[github.com/yaemikoreal/OpenNovel](https://github.com/yaemikoreal/OpenNovel)） | 4 Agent（Writer/Critic/Manager/Director）；**Canon 一致性硬校验**（世界设定文档约束）；安全围栏（递归深度/token 预算） | Director 全局分析scheduling；Canon=我们的护栏层 |
| **story-craft（TRAE 官方插件）** | 写作技法知识库：**五维人物卡 / Soul Field / 张力分层 / 反 AI 味文笔 / 契约造戏剧** | 移植进技能 `角色卡生成器 / 张力场判定 / 骨架→正文` 的内部规则；**运行时不依赖插件**（MVP §12.3 已定） |

**调研产出**（S0 交付）：一份 `docs/agent职责与prompt设计.md`——四席职责边界、每席的 system prompt 骨架、借鉴点（事件图/互评/Canon 校验）怎么落进项目，作为主笔与导演 prompt/skill 的设计稿。

### 2.1 OpenNovel 四条例接（源码级吸收设计）

> 来源：`opennovel/agents/director.py`（收束全局分析）、`core/canon_checker.py`（规则校验）、`core/causal_graph.py`（因果图）、`agents/manager.py`（状态压缩）。已评估：**不抄**他们的"按大纲直写+评分重写"（与涌现铁律冲突），只抄四条与现有闭环互补的能力。

| # | 能力 | 接入点 | 数据结构 | 时机 | 前端呈现 |
|---|---|---|---|---|---|
| ① | **世界观规则校验器**（玄幻硬约束） | 护栏链 `_guard_and_record` 加一环（0 token 正则检测） | `WorldRule {concept, constraint, rule_type, keywords, constraint_keywords}` | 事件写入前拦截打回；成文输出后 suggestion 告警 | 护栏状态显示"规则拦截 N 次" |
| ② | **伏笔账本**（三态生命周期） | Book 级新表 `foreshadows` | `ForeshadowItem {id, type, text, buried_scene, status: buried/in_progress/closed, related_char_ids, expected_close_scene, closed_at}` | 场景收束时检测更新（同④一体化） | 书页/导演台"伏笔列表"三态进度 |
| ③ | **事件因果字段** | `Event` schema 扩展（存 state_json 快照，**无需 DB 迁移**） | `caused_by: str`、`causal_pressure: float` | 回合内不填（零开销）；收束时④批量补 | —（后台数据） |
| ④ | **场景收束全局分析**（导演长程模式） | `director.py` 新增 `analyze_scene_close()`；SSE `done` 前触发 | 输入：本场事件流/张力曲线/信念变化/伏笔现状/剩余场景清单 → 输出 JSON | sim 收束（converged）后、换场前 | 收束时"导演长程汇报"（摘要/伏笔推进/下一场提示） |

**四条例接的闭环**：
```
主笔建骨架（S2）→ 世界观自然语言 + 伏笔雏形
  → LLM 解析世界观 → 结构化 WorldRule（检测仍正则，0 token）
  → 事件写入前：护栏 4 检查（人设+世界事实+规则）→ 违规打回（熔断）
  → 场景收束（S3）：analyze_scene_close
       ├─ 批量补 caused_by/causal_pressure（③）
       ├─ 伏笔三态推进/回收（②）
       ├─ 场景摘要 → 存 scene，跨场记忆/时间线
       └─ 下一场提示/调度提议 → 驱动换场
  → S4：玄幻玄幻（②①③④全量运转，护栏截"筑基用挪移"）
```

**与 OpenNovel 的本质差异（坚持不动摇）**：他们在**事后文本**校验（改下一章大纲），我们在**模拟层事件**校验（写前拦截，铁律#4）；他们的 Director 事后改计划，我们的导演回合内软引导 + 收束时把视角拉回书级校准一次。**回合内的涌现不碰。**

---

## 3. 目标架构（四席 Agent）

```
作者（从零开始）
  │ 一句话方向 / 共创对话
  ▼
[主笔 Agent] 书级 · 一次性/章节边界      ← G6 新建
  │ 产出：世界观卡 / 章节骨架(基调·张力曲线·字数) / 场景清单 / 结局集合
  ▼
book ── chapter(order_no·tone·tension_curve) ── scene(cursor_pos·stage·facts·plan_cfg·角色)
  ▼
[导演 Agent] 场景级·每回合                 ← 已有，职责收窄为"这场戏怎么演"
  │ 产出：张力 / 行动顺序 / 注入事件 / 曝光 / 举手 / 收束判定
  ▼
[角色 Agent 群] 事件级·多层演进            ← 已有
  │ 产出：感知→思考→决策→行动（涌现，护栏前拦截）
  ▼
[成文 Agent] 文本级·事后渲染              ← G8 升级
  │ 产出：旁白体小说正文（环境/动作/心理/打斗描写）
```

**四席职责（铁律：不越界）**
- **主笔=总编剧**：只管"这本书/这章怎么走"（结构、基调、悬念分配），**不写任何现场台词**
- **导演=执行导演**：只管"这场戏怎么演"（谁也动、动什么、张力起伏、何时收场），**只给方向不给台词**（铁律#1）
- **角色=演员**：在自己的感知子集+信念账本里涌现言行，**角色决策是黑盒**
- **成文=渲染器**：事件流 → 小说正文，**不改变剧情事实，只决定怎么写**

### 换场流程（沿用 v1 已确认规则）

```
sim 收束(converged) → 查本章 cursor_pos+1 的下一 scene → 建新 sim
  继承：facts(去重合并新场景 initial_facts) / beliefs / characters / last_main_actor
  重置：turn / tension / events / turn_archives / paused / action_order
→ SSE done(next_scene) → 前端自动重连下一 sim
```

---

## 4. 实施步骤

### S0 · 调研 + skill/prompt 设计（地基前的设计工作）

**目标**：读参考项目关键设计，产出四席 prompt 与 skill 的设计稿；用 `skill-creator` 建首批 skill 骨架。

- **S0.1 设计稿**：写 `docs/agent职责与prompt设计.md`（四席职责表、每席 system prompt 骨架、参考借鉴落地点：事件图/互评/Canon）。
- **S0.2 skill 创建**（MVP §12.2 首批 5 骨架 + 主笔规划；一律用 **`skill-creator`** 建在 `.trae/skills/<name>/SKILL.md`）：
  1. `主笔规划` —— 一句话方向 → 世界观 + 章节骨架（含场景清单/基调/张力曲线/结局集合）
  2. `导演调度` —— 三把巧劲 + 举手机制 + 收束判定（对齐 MVP §6）
  3. `张力场判定` —— 独立评估张力/divergence（不导演自评）
  4. `角色卡生成器` —— 五维卡 + Soul Field（story-craft 技法移植）
  5. `角色演绎` —— 感知→决策→行动（信念账本/黑板子集注入）
  6. `骨架→正文` —— 事件流 → 旁白体小说（反 AI 味文笔清单，story-craft 移植）
- **S0.3 表结构迁移**：`0002_agent_columns`（给 Book 加 `synopsis`/`worldview_json`/**`world_rules_json`**（结构化 WorldRule 数组），Chapter 加 `tone`/`tension_curve`/`word_target`，Scene 加 `stage_desc`，**新增 `foreshadows` 表**：book_id 关联 + type/status/buried_scene/expected_close_scene/related_char_ids 等列，见 §2.1②）。Event 的因果字段（③）改 `schemas/models.py` 即可，**无需迁移**（events 存 state_json 快照）。

**验收**：6 个 skill 骨架可被 SKILL 工具加载；设计稿覆盖四席 prompt；迁移后 `alembic current` 指向 0002。

---

### S1 · 数据地基补齐（写接口 + 规划 UI + 顶部接真）

**目标**：能手动完成"建书→排章→建场景→配角色"，全链路落库；导演台顶部显示真实层级。

**S1.1 后端写接口**（`backend/app/api/routers/simulation.py` + `services/service.py`）
- `POST /books`、`PUT /books/{id}`、`DELETE /books/{id}`（级联）
- `POST /books/{book_id}/chapters`、`PUT /chapters/{id}`、`DELETE /chapters/{id}`
- `POST /chapters/{chapter_id}/scenes`、`PUT /scenes/{id}`、`DELETE /scenes/{id}`
- `POST /scenes/{scene_id}/characters`（CharacterCard 校验）、`PUT /characters/{id}`、`DELETE /characters/{id}`
- Pydantic body 对齐 ORM 列；复用 `_scene_plan_cfg` 融合、`SceneNotFoundError` 等。

**S1.2 前端规划页**（新增 `frontend/src/pages/PlanningPage.tsx`，路由 `/planning`）
- 左树（书→章→场景，`/books/{id}/tree`）、右编辑（书/章/场景字段）、新建按钮调写接口。
- 场景 initial_facts 与角色卡先用 JSON 文本区 + 下拉（最小可用）。
- Sidebar 加"规划"入口。

**S1.3 DirectorPage 顶部接真**（`DirectorPage.tsx`）
- `fetchBookTree` 替换硬编码书/章/场景名；顶部加"章/场景"下拉 → `navigate(/director/:sceneId)` 重建 sim。

**S1 验收**：建/删任一层级不影响其他；顶部显示真实层级；pytest 全绿、tsc 零错。

---

### S2 · 主笔共创起点（引入主笔 Agent）

**目标**：从零开始一本书的推荐路径——跟主笔对话出骨架 → 预览 → 确认落库。

- **S2.1 后端主笔引擎**（新增 `backend/app/services/engine/chief_planner.py` + service 方法）
  - `POST /api/v1/books`（带草稿时）→ `POST /api/v1/books/{id}/plan`：主笔以书级视角（一句话方向 + 可选参考）用 `model_cheap` 产出结构化骨架 JSON → 返回预览（不落库）。
  - `POST /api/v1/books/{id}/plan/commit`：预览确认 → 批量落库 chapters/scenes（走 S1.1 的写接口逻辑）+ `worldview_json`（世界观含 `## 规则` 自然语言段）。
  - **世界观规则 LLM 解析**（§2.1①，复用 `model_cheap`）：commit 后把 `worldview_json` 的规则文本交给 LLM 转成结构化 `WorldRule[]`（rule_type/keywords/constraint_keywords），落库为 `book.world_rules_json`；后续运行时**检测用正则关键词、0 token**（OpenNovel Canon 思路，解析一次、检测永远免费）。
  - 主笔对话复用 `director_chat` 式 SSE（`plan_chat` 事件：主笔边想边说），产出骨架时给"方案 A / 方案 B"两种走向。
- **S2.2 前端"主笔规划"面板**（DirectorPanel 新增"规划"Tab或独立页）
  - 输入一句话方向 → SSE 流式看主笔推演 → 骨架预览卡片流（每章：标题/基调/张力曲线/场景清单/在场角色）→ `✓ 确认落库` / `✗ 重新规划`（预览确认，用户已拍板）。

**S2 验收**：`POST /books {方向:"玄幻·筑基弟子进秘境夺宝"} ` 返回 3 章骨架预览；确认后落库、书页结构树出现；导演台可直接进第 1 场景推演。

---

### S3 · 换场机制（一场景一 sim 闭环跑通）

**目标**：第一场收束自动开下一场，前端时间线章/场景两级，角色记得上一场。

- **S3.1 后端**：
  - `_sim_stream` break 后调 `svc.next_scene_seed(sim_id)`（仅 converged 且存在下一 scene 时返回 `next_scene`）；`svc.start_scene_after(sim_id)` 继承 facts/beliefs/characters/last_main_actor → 装配下一 scene（facts 按 id 去重）→ 建新 sim；SSE `done` 带 `next_scene`。
  - **场景收束全局分析（§2.1④）**：`director.py` 新增 `analyze_scene_close(sim)`——sim 收束后、done 前用 `model_cheap` 产出 JSON：`场景摘要`（存 scene）/ `伏笔三态更新`（写 foreshadows 表）/ `角色弧线变化` / **因果补全**（给自己 Event 批量填 caused_by/causal_pressure）/ `下一场提示`（驱动换场）。SSE `done` 前置发 `director_close` 事件给前端展示"导演长程汇报"。
  - 场景装配时：注入 `book.world_rules_json` + 已有伏笔列表 → 打进 sim（`scratch`），供护栏与角色感知使用。
- **S3.2 前端**：`useDirectorSim` 的 `done` 处理 `next_scene` → 展示"第 X 场收束，进入下一场…"（含 `director_close` 汇报卡：摘要/伏笔推进数/下一场提示）→ 自动 `createOrResumeSim({scene_id: next, resume:false})` → 重连 stream；`TimelineBar` 按场景加分组标签。

**S3 验收**：第一场 6+ 回合收束 → 自动进第二场（第二场角色能回忆第一场的事）→ 时间线两段场景；tension 从 0 拉起；未收束不换场。

---

### S4 · 玄幻完整闭环 + 成文升级

**目标**：跑通"一句话 → 主笔骨架 → 玄幻世界（境界/规则硬约束）→ 多场推演 → 旁白体玄幻文"，一章 2000-3000 字。

- **S4.1 世界观规则校验器接入护栏**（§2.1①）：护栏链 `_guard_and_record` 加第 4 步——角色行动文本/新 fact 过 `WorldRule[]` 正则检测（negation/exclusive/conditional），违规打回重演（复用熔断）；成文输出段做 suggestion 级告警（不拦截）。「筑基不能用挪移」这类硬伤在这里被拦死。护栏统计扩展"规则拦截"计数。
- **S4.2 打斗/动作事件表达**：动作事件 payload 扩展（攻防状态/距离/灵力消耗）——事件流记录，成文渲染招式。
- **S4.3 成文升级（骨架→正文 skill 接入）**：`CharacterEngine` 现有 `_default_prose`/`stream_prose` 包装为调用 `骨架→正文` skill 的成文 Agent；产出带旁白/环境/心理/招式的玄幻文风。
- **S4.4 共创决策台完整化**：面板把 `director_chat` 升级为"方案卡 + 拍板"——换场/走向/曝光选择以 A/B 形式给作者（收束分析产出的"下一场提示"作为方案来源），选择结果作为 director_hint 注入下一场（收尾项，可延后到换场跑稳）。

**S4 验收**：`curl POST /books 一句玄幻方向 → commit → 进第 1 场 → 推至第 1 章收束`，导出台上是一章约 2500 字的旁白体玄幻文；过程中无世界规则硬伤（护栏拦截数可见）；成文段有环境/动作/心理描写。

---

## 5. 关键设计决策（执行时不必再问）

1. **换场只发生在 `converged=True`**：raise_pending / paused 不触发换场。
2. **换场不复制事件流**：新 sim `events` 从空开始，跨场景记忆靠 `facts`+`beliefs`；全局剧情时间线未来由 book 层聚合（本期不做）。
3. **主笔/导演均用 `model_cheap`（flash）**，角色演绎与成文用 `model_strong`（pro）——符合"强模型做演绎、廉价模型做调度"（MVP 铁律#6）。
4. **主笔不参与回合循环**：只在建书/章节边界被调用，无持续状态；职责通过与导演的接口边界（结构化骨架 JSON）隔离。
5. **场景顺序用 `cursor_pos`**；id 沿用 `book-* / chapter-* / scene-* / sim-*`。
6. **删书/章/场景级联**（ORM 已配）；sim 表外键 nullable，删 scene 不清 sim（留历史）。
7. **skill 一律用 `skill-creator` 创建**；story-craft 仅作技法参照移植，运行时不依赖插件。
8. **前端规划页最小可用**：文本编辑 + 下拉，不做拖拽排序。
9. **世界观规则"解析一次、检测免费"**：LLM（flash）把自然语言规则转结构化 `WorldRule[]` 落库；运行时正则关键词检测 0 token（§2.1①）。
10. **因果字段收束时批量补**：回合内 Event 不填 caused_by（零开销），场景收束的 `analyze_scene_close` 一次性由 LLM 判因果链（§2.1③）。
11. **伏笔三态账本书级持有**：foreshadows 表挂在 book 下（跨场景生命周期），收束时检测更新；MVP 不做回合级检测（§2.1②）。
12. **长程校准只在收束点**：回合内的涌现软引导绝不被"全局分析"打断；导演视角拉回书级只发生在场景收束那一瞬间（§2.1④）。

## 6. 验证方式

- 后端：`uv run pytest -q` 全绿（新增 CRUD/主笔 plan/换场测试）；`uv run alembic current` 在 head（含 0002）。
- 前端：`cd frontend && npx tsc --noEmit` 零错误。
- 端到端（手测）：起后端(8000)/前端(5173) → 书页输入一句玄幻方向 → 主笔出骨架确认落库 → 导演台进第 1 场推演 → 6+ 回合收束自动换场 → 第 1 章收尾为旁白体玄幻文（约 2500 字、无规则硬伤）。