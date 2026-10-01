# 墨卷 · 涌现式小说 Agent —— 项目协作 Wiki

> **定位**：本项目长期协作文档（唯一导航源）。一张图看懂全部源码与目录，快速定位"某个功能/报错应该看哪个文件"，并对齐 S0–S4 之后的前端演进与后端补齐进度。
> **维护纪律**：每次新增/重构模块、修 bug、改架构后，**必须同步更新**本文件（含版本历史与 Bug 追踪表）。此文档作为团队持续协作的基准。

***

## 0. 版本历史

| 版本         | 日期             | 变更摘要                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | 作者            |
| ---------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| **v1.12**  | 2026-09-13     | ★★**S3 全局结构与张力：已交付（验收 9/9）** —— **零新表纯派生**（张力早已随 `TurnArchive` 归档，缺的是视图）：①`engine/global_view.py`：章级 tension_avg/peak/trend + 全书曲线（mean/std/peak_chapter）+ **7 条确定性诊断**（R1 平铺/R2 无上升/R3 高潮缺失/R4 无喘息/R5 伏笔逾期/R6 埋收失衡/R7 有正文无张力记录，每条带扣分与证据）+ 伏笔呼应网络 + `structure_score`；张力 0 视为"未评估"不按 0 计、样本不足不下结论；②`repo.list_sims_by_book`（含 ended，读已完成场景归档）；③`GET /books/{id}/global-view`；④概览页「全局张力」区块（**零依赖纯 SVG**，缺口断开不假装连线）；⑤验收脚本 `scripts/accept_s3.py`（确定性 + `--live` 真实书核对 9/9）+ 归档 `docs/验收-S3张力-原始输出.txt`；⑥修 **B19**（内存态场景判据与最新 sim 排序）。pytest **109 passed**、tsc 零错误；真实书实测：`book-7d16f6cd` 19 条伏笔 0 回收 → R6 | 主笔 Agent |
| **v1.11**  | 2026-09-13     | ★★**S2 角色口吻一致性：已交付（验收 11/11）** —— 整合 step1-6：①B16 字段错位修复（写手角色卡从"（无详细设定）"恢复为逐字注入）；②写手口吻纪律（反同质/互称一致）；③体检 `voice_findings` + 逐字证据闸门；④质检 `voice_risks` 并增 + 缺键点名反馈；⑤0-token 先验 `engine/style_checks.py`（S2 与 A3 共用底座）；⑥**新增可复跑验收脚本 `backend/scripts/accept_s2.py`**（确定性 10 项 + `--live` 真 LLM 目视对比；**实现了此前文档里只有名字的 `--live`**，退出码可接 CI）并归档原始输出 `docs/验收-S2口吻-原始输出.txt`。验收发现并修掉 4 个归属缺陷（串台/连续引号/中置归属/代词未标注），全量 **96 passed**；痛点清单 S2 行转「已交付」 | 主笔 Agent |
| **v1.10.3** | 2026-09-13    | ★**S2 step5 交付**：新增 `engine/style_checks.py`（**0-token 确定性底座**，S2 先验与 A3 反 AI 腔共用）——①切句（引号内不切）；②台词归属（名：/名前+言语动词/引号后+言语动词/同句唯一名/**紧邻引号继承**；归属窗口限定在上一引号之后，防"串台"）；③各角色句长+语气词+填充词分账；④语气词集合 Jaccard → 疑似同质（≥3 种才判定，样本小不下结论）；⑤称呼表（卡内认可 vs 待确认）；⑥`ai_tone_scan`：套话表/破折号/省略号密度（**A3 底座就绪**）。跑位=体检前，结果作为"确定性事实"注入 `_REVIEW_PROMPT` 并落 editor note `payload_json.voice_prior`；**真实数据实测**（DB 中 254 字真初稿）：陈默 7.5 字/句、李文 20.0、周婶 11.0（声线量化可区分），破折号密度 11.8/千字被标红；用例 9 例、全套 **94 passed** | 主笔 Agent |
| **v1.10.2** | 2026-09-13    | ★**S2 step4 交付**（质检口吻风险）：①`VERIFY_SCHEMA` 增 `voice_risks`（char/evidence/risk/suggestion，并增）；②`_VERIFY_PROMPT` 注入出场角色卡 + evidence **逐字摘录**硬要求；③校验器 lambda → `_validate_verify`：**逐一点名缺失字段**（纠错重试反馈可执行）+ voice_risks 非数组拦截；④防幻觉闸门泛化为 `_sanitize_grounded`（体检 voice_findings / 质检 voice_risks 共用，只留「有角色名 + 正文逐字原句」）；⑤命中项**并增一行到 `risks`**（前端零改动即见）；⑥服务层 `prose_verify`/`_bookkeep_after_save` 传角色卡、摘要带口吻风险；用例 4 例、全套 **85 passed**；真 LLM 实测命中陈默越界台词并落 note payload | 主笔 Agent |
| **v1.10.1** | 2026-09-13    | 文档：外部《涌现式六层 Agent 设计》评审结论入档 —— **不按该方案重构**（其 `agents/*` 目录与 `services/engine/*` 语义 1:1 重复、改动面涉及 65 端点 + 前端；「主笔纯回溯」会丢现有骨架/commit 资产）；仅把可用点收进《写作痛点清单与优先级》**§5 待考虑项候选池 T1–T8**（场景快照层 / 文学减法层 / 主笔回溯脉络 / 成文叙事选择 / 角色非理性授权 / 角色卡扩展字段 / 事实不可变硬闸门 / 四类约束表），池内不参与定级、触发再立方案；§10 增指向 | 主笔 Agent |
| **v1.10**  | 2026-09-13     | ★**S2 角色口吻一致性（注入 + 体检回环）交付**：①**B16 修复** `_characters_block` 字段名错位（personality/tone/bottom_line → summary/traits/voice/core_beliefs/bottom_lines）：原本每张角色卡都渲染成"（无详细设定）"，**等于没注入**（人物口吻漂移根因）；②**写手口吻纪律**：`_DRAFT_PROMPT` 增反同质/称呼一致硬要求（口号→约束）；③**体检回环**：`review_prose` 增 `characters` 形参 + `REVIEW_SCHEMA.voice_findings` + 防幻觉闸门 `_sanitize_voice_findings`（须有角色名 + 正文逐字原句，否则丢弃），服务层注入出场角色卡并把 `voice_findings` 落 editor note 的 `payload_json`；④**B17 修复**：DB 用例跨事件循环静默降级（TestClient 在自有 loop 懒建全局 engine → 后续用例 connect 抛错被 `_query_or_mem` 吞成"DB 不可用"→ 假绿/误 skip）→ conftest 按用例丢弃 engine 单例 + 探针改独立临时引擎；⑤**真 LLM 实测**：3 卡腔调逐字进 prompt、初稿三把声线可区分、越界正文被 `voice_findings` 命中；pytest **80 passed, 0 skipped（DB 起，2.8s）** | 主笔 Agent      |
| **v1.9**   | 2026-09-13     | ★主线转为「AI 写小说痛点治理」（sourcing 多项目 → 建档`docs/写作痛点清单与优先级.md` S1–S4/A1–A4/B1/C1）→ **S1 世界状态账本交付**：①**建档**：痛点清单+优先级（一文索引，方案逐条另立）；②**S1 方案**`docs/写作优化方案-S1世界状态.md`（真实代码对照=现状盘点/新表/写入/感知/验收）；③**建表**`world_states`（迁移 0012 + `WorldState` ORM + repo：`list_world_states`/`list_world_states_by_key`/`save/delete`）；④**verify_prose 扩 schema** 增 `state_deltas`（kind/name/key/value/previous_value，五字段校验）；⑤**写入端**：`_apply_verify_bookkeeping` 第三段写世界状态（真 upsert，按 `(kind,key)` 匹配复用 id，幂等）；`prose_verify`/`_bookkeep_after_save` 传 world_states + scene_no；⑥**感知注入**：`draft_prose` 增参 world_states + `_world_states_block` helper，`service.prose_draft` 只喂相关状态（cast 名匹配 character/item/term + time/numeric 全局），`chief_perceive` 增「世界状态现状」段；⑦**LLM 平台切换**：base_url 改阿里云百炼 `dashscope.aliyuncs.com/compatible-mode/v1`，key 更新，模型 `deepseek-v4-flash`（官方 DeepSeek 余额 402 弃用）；⑧**真实验收**：真实 LLM 闭环 verify→写入→写手感知全通；修复 Bug A（真 upsert 防同 key 插重）+ Bug B（item/character value 语义=持续态非动作）；pytest **17 passed（prose/bookkeep 相关）**；B14 记录 | 主笔 Agent      |
| **v1.8**   | 2026-09-02     | ★主笔 Agent 升级·阶段①记忆地基 + ②场景级部分修改 + ③纠错循环 + ④正文协作工作区 + ⑤记账 Agent + 骨架重复 bug 修复：①**书级记忆**（`book_memories` + 迁移 0007，按 book_id 隔离）+ **主笔感知装配**（`chief_perceive`：书树+记忆+灵感+伏笔→注入对话/规划）；②**场景级部分修改**（迁移 0008：`scenes`+`goal`/`content_desc`；`PUT /chapters/{id}/scenes` 整章 diff 替换 + 骨架树「编辑场景列表」）；③**纠错循环**（`engine/schema_retry.py` `validate_and_retry`：0-token 校验→反馈重试→plan 失败不落库/cards 回退模板/rules 回退空）；④**正文协作工作区**（`engine/prose.py` 四角色：✍写手/🩺体检员/🎨润色师(反AI味·只改写法)/🔍质检员(伏笔·信念·因果) + `prose_notes` 审计表（迁移 0009，作者审阅） + 7 个 `/scenes/{id}/prose/*` 接口 + Maestro「✎ 正文协作」面板）；⑤**记账 Agent**（迁移 0010 payload_json：质检明细持久化；approve verifier → `_apply_verify_bookkeeping` 真正落账——伏笔推进/信念新增/未知容错；保存正文→后台自动记账（幂等 unchanged 不触发，审计 created_by=bookkeeping））；⑥**前端记忆入口**：Maestro 右栏「书级记忆」折叠区；⑦**骨架重复修复**（B13）：`commit_book_plan` 改全量替换；设计文档 `docs/主笔Agent升级设计.md` v0.2；测试 **74 passed** + tsc 零错误 | 主笔 Agent      |
| **v1.7**   | 2026-08-28     | ★导演台交互收敛 + 前端静态兜底清理：①**导演台单页化**（v1.6 计划的"场景选择过渡页"废弃）：书+«章节/场景»分组下拉就地切换、`⟳ 新开`按钮、`localStorage('director.last')` 重进恢复，不再路由跳转整页重载；②**规划页完全并入主笔**：Maestro 中栏「书籍骨架」支持完整 书/章/场景 CRUD（建/编辑/删除/进导演台+toast），删除 `/planning` 路由与 `PlanningPage.tsx`，Dashboard/侧栏链接改指 `/maestro`；③**移除导演台静态兜底数据**：CenterStage（环境事实/角色状态/第七章示例正文）、TimelineBar（`STATIC_TURNS` 七回合）、characters.ts（`charOf` fallback 改动态空 spec）、CharRail（按 charIds 逐个 `charOf`）全部空态化 → 根治"黑板对不上新书"；tsc 零错误 + vite build 通过 | 主笔 Agent      |
| **v1.6**   | 2026-08-27     | ★导演台空黑板 + 前端交互修复：①**书架接真**（侧栏真实书目+建书弹窗+回书架主页 / Dashboard「＋新建书」）；②**Maestro 完整选书** + 灵感卡**编辑**（新 `PUT /inspirations/{id}`）；③**空黑板装配**（新场景无初始事实则 facts 空、仅 stage\_desc 作环境；无角色卡不再注入静态剧情角色；`state.empty_world` 标记）；④**导演台选角**（新 `PUT /sims/{id}/cast`，cast 不重启局面；空台引导卡**内联展开**选角面板 + 有角色后角色栏**抽屉**，双入口混合形态）；⑤**全局视角右滑层**（实时 facts/信念摘要/张力/导演提示，纯前端读 state）；修复迁移 0005（`characters.scene_id` 改可空）+ 手动迁移模式（`uv run alembic upgrade head`）；测试 48→55 用例全绿 + tsc 零错误                 | 主笔 Agent      |
| **v1.5**   | 2026-08-27     | ★信念账本独立落库 + Dashboard 概览接真：`beliefs` 表（迁移 0006，书级账本）+ sim 每回合同步 upsert（`_sync_beliefs` 挂 step/stream\_step/intervene）+ 信念完整 CRUD（含手改 `edited` 标记、channel/char 过滤）；新增 `GET /books/{id}/dashboard` 聚合（KPI/章节状态时间线/待办/金句抽取/近 4 周热力/派生健康分）；人物页信念 tab 接真（筛选/新增/编辑/删除），Dashboard 整页接真（书切换 + 空态）；repo 补齐内存态 `get_book_tree`/`get_belief`；**启动迁移改为手动**（去掉 lifespan/`start()` 的自动 `init_schema`，改由 `uv run alembic upgrade head` 显式执行）；测试 45→48 用例全绿                                 | 主笔 Agent      |
| **v1.4**   | 2026-08-27     | ★角色卡升级为**书级角色库**（v1.4）：`characters` + `book_id`（迁移 0005，旧数据 backfill 为书级）+ 场景特设角色（scene\_id 可空）；新增 `GET/POST /books/{id}/characters`，sim 装配改为 `list_characters_for_scene`（书级+特设），人物页去掉场景选择改为「选书 → 角色库」；统一 Dialog 组件替代所有 `window.confirm/prompt`（修 confirm 确定按钮 `Boolean('')===false` 导致删不掉的 bug）；**测试提速 \~70x**（conftest 补齐 patch graph/service 的 LLM client，此前每回合真调 DeepSeek 网络致全套 4min+ → 3.5s）；测试 49 用例全绿 + tsc 零错误                                                      | 主笔 Agent      |
| **v1.3**   | 2026-08-26     | ★作者介入三件套补全：`intervene` 的 `expose`（写 belief）/ `adjust_weight`（§6.1 因果律 reason 必填，内因写回）落地 + 新增 `GET /sims/{id}/inject-palette`（facts+动态目标下拉数据）；DirectorPanel 三按钮接真（内联表单）；角色卡编辑接真（`PUT /characters` + spec\_json，原人像页 UI 保留、basic 页签卡片式编辑）；测试 35→50 用例全绿 + tsc 零错误                                                                                                                                                                                                             | 主笔 Agent      |
| **v1.2**   | 2026-08-26     | ★正文落库闭环（P0）：`scenes.final_prose`（迁移 0004）+ 手动定稿 `POST /sims/{id}/finalize` + 场景/章正文查询 + 全书 md 导出；阅读台 ReaderView 接真（按章渲染+翻章+导出）；repo 内存态列表查询补齐；测试 test\_api 5→8 用例（全套 35 用例全绿）                                                                                                                                                                                                                                                                                               | 主笔 Agent      |
| **v1.1.1** | 2026-08-26     | 导演台反查修复：新增 `GET /chapters/{id}`（路由+service+repo 内存态），回归测试 `test_chapter_detail_lookup`（44 passed）；tag `v1.1.1`                                                                                                                                                                                                                                                                                                                                                              | 主笔 Agent      |
| **v1.1**   | 2026-08-26     | ★后端补齐：灵感池接口（CRUD+主笔生成+采纳持久化）、主笔共创对话 SSE、灵感→骨架落地、测试补强（43 passed）                                                                                                                                                                                                                                                                                                                                                                                                             | 主笔 Agent      |
| **v1.0**   | 2026-08-26     | ★前端里程碑：主笔共创工作台（maestro）React 化融合、纸墨双主题统一、导航改版；wiki 体系化成立（版本历史+变更日志）                                                                                                                                                                                                                                                                                                                                                                                                         | 主笔 Agent + 作者 |
| v0.2       | 2026-08-26（早）  | S4 玄幻闭环：世界规则 0-token 校验、成文升级、换场续场、伏笔三态                                                                                                                                                                                                                                                                                                                                                                                                                                      | 主笔 Agent      |
| v0.1       | 2026-08-26（初始） | 初版导航文档（S0–S4 后基线）                                                                                                                                                                                                                                                                                                                                                                                                                                                           | 主笔 Agent      |

> 版本规则：功能交付/架构变更 → 升版本；纯 bug 修复 → 只记 Bug 追踪表不下版本。

***

## 1. 项目定位与数据模型

四层目录体系，从"书"一直下钻到"推演回合"：

```
Book(书) → Chapter(章) → Scene(场景=一台戏) → Simulation(推演实例,每场景一个)
                                        → Event(回合事件,挂 sim 下)
                                        → Foreshadow(伏笔,跨场景生命周期)
                                        → WorldRule(世界观规则,玄幻硬约束)
```

| 层  | 载体                           | 关键文件                                                                             |
| -- | ---------------------------- | -------------------------------------------------------------------------------- |
| 书  | `books` 表                    | [book.py](file:///e:/novel_desk-agent/backend/app/db/models/book.py)             |
| 章  | `chapters` 表                 | [chapter.py](file:///e:/novel_desk-agent/backend/app/db/models/chapter.py)       |
| 场景 | `scenes` 表                   | [scene.py](file:///e:/novel_desk-agent/backend/app/db/models/scene.py)           |
| 角色 | `characters` 表               | [character.py](file:///e:/novel_desk-agent/backend/app/db/models/character.py)   |
| 推演 | `simulations` 表 + `sim` 内存黑板 | [simulation.py](file:///e:/novel_desk-agent/backend/app/db/models/simulation.py) |
| 伏笔 | `foreshadows` 表              | [foreshadow.py](file:///e:/novel_desk-agent/backend/app/db/models/foreshadow.py) |
| 世界状态 | `world_states` 表（S1·战力/道具/时间/术语/数字） | [world_state.py](file:///e:/novel_desk-agent/backend/app/db/models/world_state.py)（迁移 0012） |

***

## 2. 目录全景地图

```
e:\novel_desk-agent
├── backend/                     # FastAPI 后端（Python 3.12, uv venv）
│   ├── app/
│   │   ├── main.py             # 应用组装入口（CORS/日志/lifespan/异常兜底）
│   │   ├── config.py           # 配置中心（读根目录 .env，前缀 NOVEL_）
│   │   ├── api/
│   │   │   ├── routers/simulation.py   # 全部 HTTP+SSE 路由（prefix=/api/v1）
│   │   │   └── deps.py         # 依赖注入（get_service/get_repo）
│   │   ├── services/
│   │   │   ├── service.py      # 服务层：启动/step/换场/四层CRUD/主笔plan 编排
│   │   │   ├── engine/         # ★核心引擎（多Agent）
│   │   │   │   ├── graph.py           # LangGraph 回合节点链（单回合图）
│   │   │   │   ├── director.py        # 导演：计划/调权/举手/收束/长程分析
│   │   │   │   ├── character.py       # 角色引擎：感知→决策→护栏→事件
│   │   │   │   ├── world.py           # 世界黑板：facts/beliefs/冲突消解
│   │   │   │   ├── chief_planner.py   # 主笔：书骨架规划（plan/commit/规则解析）
│   │   │   │   ├── prose.py           # 正文四角色：写手/体检员/润色师/质检员(+S1世界状态 state_deltas)
│   │   │   │   └── world_rules.py     # 世界观规则 0-token 校验器(S4)
│   │   │   └── llm/client.py   # LLM 客户端（阿里云百炼 OpenAI兼容，强/廉模型，流式）
│   │   ├── db/
│   │   │   ├── repo.py         # 数据访问（ORM/内存态双模式）
│   │   │   ├── models/         # ORM 表：book/chapter/scene/character/foreshadow/belief/instruction/chat_history/prose_note/memory/world_state
│   │   │   ├── engine.py       # async engine（pgvector）
│   │   │   └── seed.py         # 种子数据
│   │   ├── schemas/models.py   # 领域模型（SimulationState/WorldState…）
│   │   ├── scenarios/          # 场景剧本模板（betrayal_night…）
│   │   └── errors.py           # 领域异常 → HTTP
│   ├── alembic/versions/       # 迁移：0001 基线 / 0002 新列+伏笔表 / … / 0007 书级记忆
│   └── tests/                  # 7 个测试文件（见第 6 节）
├── frontend/                   # ★React 18 + Vite 前端（真版）
│   └── src/
│       ├── pages/              # 路由页面（见第 5 节，★含 MaestroPage）
│       ├── components/
│       │   ├── backoffice/     # Sidebar / ThemeToggle
│       │   └── director/       # 导演台 5 组件
│       ├── hooks/              # useDirectorSim/useDirectorChat（SSE）
│       ├── api/novel.ts        # 四层 API 封装
│       ├── types/types.ts      # SSE/状态 TS 类型 + API_BASE
│       ├── theme/              # 纸墨双主题 Context（data-theme 驱动）
│       └── styles/             # tokens/app/dashboard/characters/★maestro.css
├── webapp/                     # 旧静态版（设计稿对照，React 的前身，含 maestro 融合 demo）
│   └── (index/dashboard/characters/★maestro.html + js/ + css/ + .bak 备份)
├── docs/                       # ★需求与设计文档（同层 MVP 规范）
│   ├── MVP设计.md / prompt核心设定.md
│   ├── agent职责与prompt设计.md
│   └── ★本项目Wiki.md           ← 本文档（唯一导航源）
├── .trae/
│   ├── documents/              # 架构落地计划（book-chapter-scene…v1）
│   ├── specs/                  # 官方用例 spec（backend-layered-refactor…）
│   └── skills/                 # 6 个项目级 skill（SKILL.md 骨架）
├── docker-compose.yml          # PG16+pgvector 容器（novel-ink-db:5433）
├── .env                        # LLM key/base/model（根目录）
└── pyproject.toml / uv.lock    # Python 依赖（uv 管理）
```

***

## 3. 运行时拓扑

```
浏览器 (http://127.0.0.1:5173)  ── CORS ──>  uvicorn (127.0.0.1:8000)
  │  ├─ REST  /api/v1/*                FastAPI app.main:app
  │  └─ SSE   /api/v1/sims/{id}/stream ──> Graph (单回合循环)
                                              │  LLM: 阿里云百炼（强=演绎/廉=导演·成文，deepseek-v4-flash）
                                              ↓
                          PG16+pgvector  novel-ink-db (5433, 卷 novel-ink-data)
```

- 前端固定连 `http://127.0.0.1:8000`（[types.ts](file:///e:/novel_desk-agent/frontend/src/types/types.ts) `API_BASE`，避免 localhost→IPv6 坑）

- 后端 CORS 白名单：8000/5173 两个 origin（[main.py](file:///e:/novel_desk-agent/backend/app/main.py#L52-L54)）

- DB 未就绪时 `persist` 走内存态兜底，测试/演示不依赖容器

***

## 4. 后端核心链路（Bug 高发区，先看这里）

### 4.1 启动 / 步进 / SSE 流（作者"开始推演"）

```
[POST] /api/v1/sims ─> service.create_sim
  rest ->resume_latest(exclude ended=true)      ← 历史死锁修复点
  new  ->_inject_world_rules + build_graph(PlanCfg)
[POST] /sims/{id}/stream ─> simulation._sim_stream   ← SSE 逐事件转发
  turn_start → character_perceive(先亮)
  → svc.stream_step() = graph astream(custom) 每节点出一事件
  → turn_end → 循环 → 收束时:
      director_close (analyze_scene_close) + done(next_scene)
```

事件契约（前端 useDirectorSim 按 kind 分派）：`turn_start / character_perceive / character_think / character_act / director_* / director_close / turn_end / done`。

### 4.2 单回合节点链（graph.build\_graph，5 节点）

```
director_plan ─> _apply_guidance(曝光→信念/调权/注入) ─> character_phase
   ─> refresh_world(冲突回合) ─> render_prose(180-280字旁白)
```

- 角色串行决策（保证行动事件先于另一角色感知）

- 护栏 `_guard_and_record` 四层：人设 → 世界事实 → **(S4) 世界观规则** → 熔断降级

- 成文 stream\_cheap\_text() token 级流式（字符实时打字效果）

### 4.3 收束 / 换场（场景切下一场景）

```
导演收敛判定(≥6回合 → LLM 两次同结局)
→ analyze_scene_close: 因果补全(caused_by/causal_pressure) + 伏笔三态推进 + 场景摘要 + 下一场提示
→ next_scene_seed → SSE done 带 next_scene
→ 前端自动建新 sim 续场（useDirectorSim 内）
```

### 4.4 主笔规划（书骨架 → ★工作台入口）

```
[POST] /books/{id}/plan        -> chief_planner 生成 worldview+chapters+foreshadow
                                     上下文 = chief_perceive(书树+记忆+已采纳灵感+伏笔+世界状态)   ← v1.9 增世界状态
[POST] /books/{id}/plan/commit -> 落库（章/场景/伏笔/规则；★全量替换：先删该书旧章/场再重建，B13 修复）
GET/POST /books/{id}/memories · PUT/DELETE /memories/{id}  → 书级记忆 CRUD（v1.8）
PUT /chapters/{id}/scenes      → 场景级全量替换（整章场景数组，服务端 diff 增删改；v1.8 阶段②）
```

> v1.8 起主笔从「一次性函数」升级为「有感知/记忆/纠错循环的 Agent」：`chief_chat_stream` 与 `plan_book` 走 `chief_perceive` 感知装配（按 book_id 隔离记忆，多书不串味）；`plan_skelly/generate_cards/parse_world_rules` 输出经 `validate_and_retry` 纠错（schema 校验→带错误反馈重试→plan 失败不落库/cards 回退模板/rules 回退空）。
> 升级路线见 `docs/主笔Agent升级设计.md`（记忆✔ → 部分修改✔ → 纠错循环✔ → 正文协作 → 记账 Agent → 元技能 → tool-calling 循环评估）。

### 4.5 导演台书树反查（scene → chapter → book，v1.1.1 补齐 / v1.7 单页化）

```
DirectorPage 挂载 → 优先取路由 state / localStorage('director.last') 的 book_id
  无则：GET /scenes/{id} → chapter_id → GET /chapters/{id} → book_id   ← B11 修复点（曾缺端点 405）
  → GET /books/{id}/tree → 顶栏书标题 + 场景切换下拉
v1.7 起：书/场景切换 = 页内 state 切换（不走路由、不整页重载）；场景下拉旁 `⟳ 新开`
```

### 4.6 世界状态账本（S1 · verify 扩 schema → 写入 → 感知）

```
作者保存正文 final_prose
   ▼
save_scene_prose (幂等：unchanged 不触发)
   ▼ 后台
_bookkeep_after_save ── world_states + scene_no ──> verify_prose 增参 world_states
   ▼ LLM 产出 opinion（四表 + state_deltas）                       ← v1.9 新增 state_deltas
   ▼
_apply_verify_bookkeeping 第一/二段：伏笔推进 + 信念新增
   第三段：写世界状态（真 upsert：按 (book_id,kind,key) 匹配既有行复用其 id，
           previous_value 取库中当前值；未命中则 ws-<hash> 新建）  ← B14 修复点
   ▼
感知回读：
  prose_draft 只喂相关状态（character/item/term·cast名匹配 + time/numeric 全局）
  chief_perceive 第6段「世界状态现状」（edited=false 前30条）
```

> S1 定级/方案见 `docs/写作痛点清单与优先级.md`（S1）+ `docs/写作优化方案-S1世界状态.md`。当前只覆盖「角色状态 · 单态 upsert」层；append-only 时序流、四表（道具链/时间锚/术语/关键数字）自动校验列为后续触发项。

***

## 5. 前端模块地图（★ v1.0 更新）

| 路由                    | 组件             | 职责                                                              |
| --------------------- | -------------- | --------------------------------------------------------------- |
| `/`                   | HomePage       | 书架 → 选书 → 场景 → 进导演台                                             |
| `/dashboard`          | DashboardPage  | 概览工作台（静态样表）                                                     |
| `/director/:sceneId?` | DirectorPage   | ★ 导演台：组装 5 组件；**单页下拉切换场景/书，无独立选择页**（v1.7）                       |
| `/maestro`            | ★MaestroPage   | ★主笔共创工作台：三栏（灵感池+骨架树+共创对话）+ 底部张力曲线；**中栏骨架支持书/章/场景完整 CRUD**（v1.7） |
| `/characters`         | CharactersPage | 人物页（静态样表）                                                       |

**侧边导航**（[Sidebar.tsx](file:///e:/novel_desk-agent/frontend/src/components/backoffice/Sidebar.tsx)）：概览 → **☆主笔创作(/maestro)** → 设定 → 人物 → 大纲 → 伏笔 → 章节 → 记忆包 → 体检。新增"主笔创作"替换原"规划"；原 `/planning` 路由已删除（v1.7，功能并入 MaestroPage）。

**MaestroPage 内部结构**（[pages/MaestroPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/MaestroPage.tsx)）：

| 区域 | 内容                                       | 数据来源                                                    |
| -- | ---------------------------------------- | ------------------------------------------------------- |
| 顶栏 | 方向输入 + 展开面板 + 「让主笔构思」+ agent 状态          | plan API / mock 回退                                      |
| 左栏 | 灵感池（灵感卡⇄已采纳）                             | ★真实 /books/{id}/inspirations（列表/generate/采纳 PATCH，v1.1） |
| 中栏 | 书籍骨架树（章/场景，可展开折叠）                        | ★真实 /books/{id}/tree                                    |
| 右栏 | 主笔共创对话（骨架预览开关/工具角标）+ **书级记忆折叠区**（列表/添加/编辑/删除，v1.8） | ★真实 /books/{id}/chief/chat SSE（v1.1）+ /books/{id}/memories（v1.8） |
| 底部 | 全书张力曲线（canvas，★主题跟随 MutationObserver 重绘） | 本地静态数据                                                  |

**导演台组件拆分**（[components/director](file:///e:/novel_desk-agent/frontend/src/components/director)）：

| 组件            | 职责                                                                  | 数据来源                 |
| ------------- | ------------------------------------------------------------------- | -------------------- |
| CharRail      | 角色卡（情绪条/信念/think/act）；按 `state.charIds` 逐个 `charOf()` 渲染任意书角色（v1.7） | SSE character\_\*    |
| CenterStage   | 中央舞台（黑板 events / 成文 prose / 收束汇报卡）；**空态引导，无静态剧情/示例正文兜底**（v1.7）      | SSE                  |
| DirectorPanel | 导演提示 + 举手卡（同意/拒绝）                                                   | SSE director\_\*     |
| TimelineBar   | 底部回合时间线 + 播放/单步/**回退按钮**；**无数据时空态提示**（v1.7）                         | sim state + archives |
| ReaderView    | 沉浸阅读视图                                                              | 成文                   |

前端状态通道：

- `useDirectorSim`（[hooks](file:///e:/novel_desk-agent/frontend/src/hooks/useDirectorSim.ts)）：SSE 全量分派 + 自动续场 + 回退归档

- `useDirectorChat`：作者↔导演共创对话（token 级流式）

**样式体系（★纸墨双主题已全站统一）**：

- [tokens.css](file:///e:/novel_desk-agent/frontend/src/styles/tokens.css)：`<html data-theme="ink|paper">` 驱动全部 CSS 变量

- [maestro.css](file:///e:/novel_desk-agent/frontend/src/styles/maestro.css)：`--maestro-*` 变量同样挂 `[data-theme]` 双套（墨深/纸浅），顶栏渐变、badge、卡片底色全用主题变量；canvas 曲线从 CSS 变量读色

- 切换入口：各页顶栏 `ThemeToggle`（☯ 墨/纸）

***

## 6. 测试地图（backend/tests）

| 文件                          | 覆盖                                    | 跑法                |
| --------------------------- | ------------------------------------- | ----------------- |
| test\_core.py               | 世界黑板/护栏/推理核心（内存态）                     | 快速                |
| test\_api.py                | API 冒烟 + 启动→step→状态闭环（TestClient+内存态） | 快速                |
| test\_db\_orm.py            | 真实 PG：基线表/级联/pgvector 兼容              | **DB 不可用自动 skip** |
| test\_director\_converge.py | 导演回合护栏（<3 不收束、LLM 收敛判定）               | 快速                |
| test\_scene\_hierarchy.py   | 章→场景层级                                | 快速                |
| test\_world\_rules.py       | 世界观规则 0-token 校验（negation/exclusive）  | 快速                |
| test\_schema\_retry.py      | 主笔纠错循环：validate\_and\_retry 重试/熔断 + plan/cards/rules 三接入点（内存态） | 快速                |
| test\_prose\_studio.py      | 阶段④正文协作：四角色无 LLM 回退 + prose\_notes 生命周期（approve 记账/reject 不写库）+ 幂等保存（内存态） | 快速                |
| test\_inspiration.py        | 灵感池 CRUD + plan inspiration\_ids 透传      | 快速                |
| test\_style\_checks.py      | **0-token 口吻先验**（S2 step5 / A3 共用底座）：切句（引号内不切）/台词归属（含连续引号同人、防串台）/句长与语气词分账/同质判定/称呼表/套话与破折号密度 | 快速（纯函数，无 LLM/DB） |
| test\_global\_view.py       | **S3 全局张力**（纯函数 + 内存态装配）：曲线/峰值派生、R1-R7 七条诊断、护栏（样本不足不报/张力 0 不按 0 计）、服务装配与"每场景取最新 sim" | 快速（纯函数）+ 内存态 |

> 2026-08-26 v1.1.1 基线：**44 passed, 6 skipped**（6 个 skipped = test\_db\_orm 需 docker；test\_api 含章节反查回归 `test_chapter_detail_lookup`）。
> 全量测试约 6 分钟（部分用例含 sleep），快速迭代可只跑 `pytest -q tests/test_core.py tests/test_world_rules.py`.
> 前端检查：`cd frontend && npx tsc --noEmit`（当前零错误）。
>
> **验收脚本**：`python backend/scripts/accept_s2.py [--live]`（S2 口吻一致性；退出码 0=全通过）。
> **2026-09-13 基线（v1.11）**：**96 passed, 0 skipped**（DB 已起，约 3s）；S2 验收 **11/11**。
> **2026-09-13 基线（v1.10.3）**：**94 passed, 0 skipped**（DB 已起，3.0s）。演进：69/6（B17 静默降级）→ 80/0（B17 修复）→ 85（S2 step4 + 结构对齐守卫）→ **94**（S2 step5 先验 9 例）。DB 未起时 DB 用例优雅 skip、其余全绿。

***

## 7. Bug 追踪表（当前已知问题 / 已修复记录）

| #   | 状态   | 问题                                                            | 根因/证据                                                                                                                                          | 修复位置                                                                                                                                                                                                                                                                                                                                                                                                                                |
| --- | ---- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B19 | ✅ 已修 | **内存态兜底与 DB 分支语义不一致**（DB 一挂就丢视图）：①`list_scenes_by_chapter`/`get_book_tree` 把 `cursor_pos` 当**场景类型标记** → 未显式带该键的场景在内存态**整体不可见**（书树 scenes 空、概览与 S3 全局视图丢场景）；②新增的 `list_sims_by_book` 内存分支无"最新优先"排序 → 与 DB 的 `updated_at DESC` 不一致，会取到旧局 | ①两处判据改用 `chapter_id`（场景必填外键）并排除 `book_id`（章的特征）；②内存分支用插入序逆序模拟 `updated_at DESC`。暴露路径：S3 装配测试断言 `list_scenes_by_chapter == [s1]` 时发现列表为空 | [repo.py](file:///e:/novel_desk-agent/backend/app/db/repo.py) 三处内存分支；回归见 [test_global_view.py](file:///e:/novel_desk-agent/backend/tests/test_global_view.py) `test_service_assembles_tension_from_sim_archives` |
| B18 | ✅ 已修 | **DB 表结构与 ORM 静默漂移**：`beliefs` 仍是旧 sim 级结构（复合主键 sim_id/char_id/fact_id/source_event_id/ts），`repo.list_beliefs` 恒抛 `column beliefs.id does not exist` → `_query_or_mem` 静默落内存态 → **v1.5 信念账本在 DB 模式实际失效（0 报错、0 落库）**；`chat_histories` 缺 `updated_at` | 迁移 0001 用 `Base.metadata.create_all` 建表、0006/0011 用 `op.create_table`（表已存在即跳过）→ 在「旧结构已存在的库」上「迁移跑过、结构从未变」；实测 `\\d beliefs` = sim_id 复合主键、无 id/book_id，而 `alembic_version` 已是 0012。发现路径：S2 step4 真 LLM 实测时 `list_beliefs` 报错暴露 | 新增 [0013_reconcile_schema_drift](file:///e:/novel_desk-agent/backend/alembic/versions/0013_reconcile_schema_drift.py)（**幂等**：按实际列判断；beliefs 重建为书级并回填 `book_id`（↔simulation）+ App 同款 md5 id，chat_histories 补 updated_at）+ [test_db_orm.py](file:///e:/novel_desk-agent/backend/tests/test_db_orm.py) 新增**结构对齐守卫** `test_schema_matches_orm_no_drift`（漂移即红灯）。修后 alembic=0013、漂移 **0 表**、账本读写真落库，全量 **85 passed** |
| B17 | ✅ 已修 | DB 用例静默退化：跟在 TestClient 用例后面跑就报"DB 不可用"skip，DB 路径实际没被覆盖 | `app/db/engine.py` 全局单例 engine 绑定在"第一个用到它的用例"的事件循环上（TestClient 跑在自有 loop），后续用例在 session loop 复用 → asyncpg "attached to a different loop" → `repo._query_or_mem` 与测试探针都 except 吞掉 → 降级内存态/误 skip。实测：单跑 `test_db_orm` 4 passed，跟 `test_api` 后跑 1 skipped | [conftest.py](file:///e:/novel_desk-agent/backend/tests/conftest.py) 新增 autouse `_isolate_db_engine`（按用例丢弃 engine/sessionmaker 单例）+ [test_db_orm.py _db_ready](file:///e:/novel_desk-agent/backend/tests/test_db_orm.py#L36) 改独立临时引擎探测；修后 `test_api + test_db_orm` 22 passed 0 skipped，全量 **80 passed 0 skipped**（v1.10） |
| B16 | ✅ 已修 | 写手 prompt 的角色卡恒渲染为"（无详细设定）"，角色卡等于没注入（人物口吻漂移的根因） | `prose.py:62-64` 读 `personality`/`tone`/`bottom_line` 旧字段名，与 `CharacterCard`（`summary/traits/voice/core_beliefs/bottom_lines`）不符；全后端仅此 3 行用旧名（grep 证据）；测试因 `spec_json:"{}"` 恰好绕过 | [prose.py _characters_block](file:///e:/novel_desk-agent/backend/app/services/engine/prose.py#L49)：字段对齐 + 空卡显式标注；回归用例 `test_draft_injects_character_card_fields` / `test_draft_marks_blank_card_explicitly`（纯 bug 修复，不升版本） |
| B15 | ✅ 已修 | 主笔对话回退路径只发 1 帧 token，前端"逐字渲染"回退失效（`test_chief_chat_sse_fallback` 变红） | v1.8/v1.9 重写 `chief_chat_stream` 时把原先两段 `yield` 合并为一次性 `yield fallback`；测试断言 ≥2 帧失败 | [service.py chief_chat_stream](file:///e:/novel_desk-agent/backend/app/services/service.py#L966)：回退文案恢复两段增量下发（v1.9.1） |
| B14 | ✅ 已修 | 世界状态同 key 写入插重复行 / previous\_value 取错 / item value 被写成动作 | ①`_apply_verify_bookkeeping` 用 hash(id 格式假设) 定位行，历史/手建任意 id 不匹配 → 同 key 变两行；②item value 由 LLM 填了"动作(震颤/挥剑)"而非"状态(携带/缺失)" | [service.py _apply_verify_bookkeeping](file:///e:/novel_desk-agent/backend/app/services/service.py#L848)：真 upsert 按 `(kind,key)` 匹配既有行复用 id + previous\_value 取库中当前值；[prose.py _VERIFY_PROMPT](file:///e:/novel_desk-agent/backend/app/services/engine/prose.py#L126)：明确 item/character value=持续态非动作（v1.9）；真实验收确认无重复行（v1.9） |
| B13 | ✅ 已修 | 书籍骨架重复：同一章/场反复出现（测试书 20 个重复章/55 重复场景）                        | `commit_book_plan` 按标题去重（增量追加），主笔（LLM）每次 re-plan 标题措辞一变 → 旧章匹配不上 → 新章被追加、旧的也不删 | [service.py commit_book_plan](file:///e:/novel_desk-agent/backend/app/services/service.py#L359-L411)：改**全量替换**（先删该书旧章节/场景再按 plan 重建）；DB 清库（book-7d16f6cd：20 章/55 场）                                                                                                                                                                                                                       |
| B12 | ✅ 已修 | 选新书进导演台，中间黑板仍显示旧"雨夜书房"剧情/陈默李文/第七章示例正文，改不了对不上                  | 4 个前端组件在无真实数据时回退硬编码旧场景数据：CenterStage（环境事实/角色状态/示例正文）、TimelineBar（`STATIC_TURNS`）、characters.ts `charOf` fallback `CHARS[0]`、CharRail 裸 ID stub | [CenterStage.tsx](file:///e:/novel_desk-agent/frontend/src/components/director/CenterStage.tsx) / [TimelineBar.tsx](file:///e:/novel_desk-agent/frontend/src/components/director/TimelineBar.tsx) / [characters.ts](file:///e:/novel_desk-agent/frontend/src/components/director/characters.ts) / [CharRail.tsx](file:///e:/novel_desk-agent/frontend/src/components/director/CharRail.tsx)：全部空态化，`charOf` fallback 改动态空 spec（v1.7） |
| B11 | ✅ 已修 | 无 book\_id 直入导演台（URL/刷新）时书标题/树缺失                              | 后端未暴露 `GET /chapters/{id}`（405）；repo.get\_chapter 内存态缺 `_mem` 分支                                                                               | [simulation.py](file:///e:/novel_desk-agent/backend/app/api/routers/simulation.py#L133-L140) 新增路由 + [repo.py](file:///e:/novel_desk-agent/backend/app/db/repo.py) 内存态兜底；回归测试 `test_chapter_detail_lookup`（v1.1.1）                                                                                                                                                                                                                   |
| B10 | ✅ 已修 | maestro 工作台「让主笔构思」结束后按钮卡在"主笔构思中…" disabled                    | 真实 plan API 成功分支漏 `setIdeating(false)`（只有演示回退分支复位）                                                                                             | [MaestroPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/MaestroPage.tsx#L262-L288) 两分支都复位                                                                                                                                                                                                                                                                                                                                  |
| B9  | ✅ 已修 | maestro 固定深色，不随纸墨主题切换                                         | `--maestro-*` 挂在 `:root` 硬编码墨色                                                                                                                 | 拆成 `[data-theme="ink"/"paper"]` 双套 + canvas 读 CSS 变量 + MutationObserver 重绘                                                                                                                                                                                                                                                                                                                                                          |
| B8  | ✅ 已修 | webapp/ 静态版被错误融合 maestro（应融合 React 版）                         | 消息歧义：目标文件是 webapp 三件套，真意图是 React frontend                                                                                                      | React 建 MaestroPage/style + 路由 + 导航；webapp 保留 .bak 备份                                                                                                                                                                                                                                                                                                                                                                               |
| B7  | ✅ 已修 | 规划页点"plan"右侧空白                                                | `.app-shell flex-direction: column` 导致 sidebar 与 main-col 上下堆叠，内容区高度=0                                                                         | [app.css#L31](file:///e:/novel_desk-agent/frontend/src/styles/app.css#L31) 改 `row`                                                                                                                                                                                                                                                                                                                                                  |
| B6  | ✅ 已修 | `.venv\Scripts\python.exe` 报 0xC0000135 (DLL 缺失)，uv/pytest 全挂 | venv 启动器损坏，缺 python312.dll                                                                                                                     | 用 uv 缓存 base python.exe + 拷 python312.dll/vcruntime140 覆盖（已备份 `.bak`）                                                                                                                                                                                                                                                                                                                                                               |
| B5  | ✅ 已修 | 收敛后无续场，只 3 回合没下文                                              | 场景无 next\_scene 机制                                                                                                                             | S3.2：analyze\_scene\_close + SSE done 带 next\_scene + 前端自动续场                                                                                                                                                                                                                                                                                                                                                                        |
| B4  | ✅ 已修 | 真实 LLM 回合 1 提前收束                                              | 收敛判定缺最低回合护栏                                                                                                                                    | test\_director\_converge：`turn<3 → 不收束`                                                                                                                                                                                                                                                                                                                                                                                             |
| B3  | ✅ 已修 | 历史 sim 死锁（ended=true+converged=false 挡恢复）                     | resume 未排除 ended                                                                                                                               | resume\_latest 排除 ended=true + 清库                                                                                                                                                                                                                                                                                                                                                                                                   |
| B2  | ✅ 已修 | SSE 回合级整批输出，无实时感                                              | graph 用默认 stream\_mode                                                                                                                         | 改 `astream(custom)` + `get_stream_writer()` 事件级                                                                                                                                                                                                                                                                                                                                                                                     |
| B1  | ✅ 已修 | 从 backend/ 启动读不到根 .env                                        | config env\_file 相对路径                                                                                                                          | 改绝对路径指向根 `.env`                                                                                                                                                                                                                                                                                                                                                                                                                     |

**当前环境状态（2026-08-26 收盘）**：后端 8000 **运行中**、数据库容器 5433 **运行中**、前端 vite **运行中**（端口占用重启需先清 PID）。

**当前环境状态（2026-09-13 复盘）**：三件套**全部运行中** —— DB 容器 5433（healthy）、后端 8000（`--reload`）、前端 5173；pytest **80 passed / 0 skipped**。存量改动已分 8 批提交、工作区干净；当日为清除硬编码 DB 密码**重写全部历史**（所有 commit hash 变更，见 §11 注）。

### 7.1 排查 bug 的标准动作

1. 看日志：`backend/app/logs/app.log`（业务 INFO 全量，5MB×3 轮转；`main.py` 的 `_LOG_DIR = app/`），比终端全
2. 确认服务三连：`netstat -ano | findstr ":5433"` / `:8000` / `:5173`
3. 跑最小测试集：`cd backend && .venv\Scripts\python.exe -m pytest -q test_core.py test_world_rules.py`
4. 前端类型：`cd frontend && npx tsc --noEmit`
5. 项目已建 git（v1.1 起）：改动及时提交，大改前确认 `git status` 工作区干净

***

## 8. 启动手册（用户自启）

```powershell
# 1) 数据库：先开 Docker Desktop，再
cd e:\novel_desk-agent
docker compose up -d            # 容器 novel-ink-db → 5433（镜像 rag-kb-postgres:pg16 本地已有）

# 2) 后端（backend 目录，用修复后的 venv）
cd backend
..\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000

# 3) 前端（frontend 目录）
cd frontend
npm run dev                     # 打开 http://127.0.0.1:5173

# 可选：灌种子数据
cd backend
..\.venv\Scripts\python.exe -m app.db.seed
```

验证：`Invoke-RestMethod http://127.0.0.1:8000/health` → `{"status":"ok"}`

**LLM 可选**：根目录 `.env` 未配 `NOVEL_OPENAI_BASE_URL/KEY` 时走确定性回退（不需要 key 也能跑通闭环）；配了则用 `NOVEL_MODEL_STRONG`（角色演绎）+ `NOVEL_MODEL_CHEAP`（导演/成文/主笔）。当前（v1.9 起）平台=**阿里云百炼**：`NOVEL_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `deepseek-v4-flash`（官方 DeepSeek 因余额 402 弃用）。

***

## 9. 改代码铁律（浓缩自项目记忆，长期有效）

- **版本管理**：项目已建 git（v1.1 起，仓库身份 `lvco`）；改动及时提交、里程碑打 tag（当前 `v1.1.1`），大改前确认工作区干净。历史教训：无 git 时期覆盖 index.html 不可逆丢失

- **venv 规范**：一律用 `.venv`，不混系统 Python（系统 python 缺 pgvector 等依赖）

- **测试规范**：pytest 默认 FakeLLM（无网络无消耗）；真 LLM 需 `--live`

- **DB 约束**：容器必须 `rag-kb-postgres:pg16` + 卷 `novel_ink-data`；密码含 `@` 用 `quote()` 编码

- **架构约定**：四层 prompt 分层（书籍/导演/环境感知/角色卡）；角色最终 Prompt = ③环境 + ④角色卡 + ②导演相关信念

- **主题规范**：新增面板样式必须走 `[data-theme]` CSS 变量（tokens.css 体系），禁止硬编码色值

- **文档为准**：`docs/prompt核心设定.md`（和 MVP设计.md 平级）是规范依据；本 Wiki 是导航与协作基准

***

## 10. 后续路线（v1.1.1 盘点后 · 下一阶段）

> **2026-09-02 最新主线**：★**主笔 Agent 升级**（设计文档 `docs/主笔Agent升级设计.md` v0.1，阶段①记忆地基已交付）→ ②场景级部分修改 → ③纠错循环 → ④正文协作（主笔生成↔作者手写，导演台涌现暂缓） → ⑤记账 Agent → ⑥元技能 → ⑦tool-calling 循环评估。
>
> **2026-09-13 主线切换**：主笔升级基本落地后，转攻「AI 写小说痛点治理」。已建档 `docs/写作痛点清单与优先级.md`（S1–S4/A1–A4/B1/C1），**S1 世界状态账本已交付**（v1.9）；下一步沿清单推进：S2 角色口吻 → S3 全局结构/张力 → S4 涌现式嵌入（分层+桥接）等（每一条先出独立方案文档再落地）。

**已交付（原 P0 全部完成，v1.1）**：灵感池接口（CRUD + 主笔 generate + 采纳持久化）、主笔共创对话 SSE、灵感→骨架落地（commit 引用已采纳灵感）、前端 MaestroPage 全部接真。

> **2026-09-13 待考虑项池**：外部「六层 Agent」方案的可用点已收进《写作痛点清单与优先级》**§5（T1–T8）**——不参与定级、不改变推进顺序，触发条件成立或被点名时才立独立方案文档。主线仍按 **S1 → S2 → S3 → S4 / A / B / C** 推进。

**导演台全流程对齐盘点（2026-08-26 v1.1.1）**：启动/恢复（四层装配）、SSE 事件级流、播放/暂停/单步、举手同意/拒绝、导演对话（token 流式）、时间线查看/回退、收束汇报（director\_close）、自动续场（next\_scene）、书树反查 + 场景切换 —— **前后端已全部对齐，无缺端点**。

剩余缺口（按"推演 → 成书"闭环排序）：

| 优先级    | 项           | 现状 → 目标                                                          | 涉及文件                                       |
| ------ | ----------- | ---------------------------------------------------------------- | ------------------------------------------ |
| ~~**P0**~~ | ~~**正文落库/导出**~~ | ✅ **已交付（v1.2）**：`scenes.final_prose` + 手动定稿 + 章节维度聚合 + 全书 md 导出 | service + repo + 迁移 0004 |
| ~~P1~~     | ~~阅读台接真~~       | ✅ **已交付（v1.2）**：ReaderView 渲染已落库章节正文 + 翻章 + 导出                | ReaderView.tsx + 查询端点 |
| P1     | 伏笔追踪面板      | 仅收束汇报文字提及 → 独立伏笔面板（三态时间线，`GET /books/{id}/foreshadows` 已有，前端未消费） | 新组件 + DirectorPage/侧边"伏笔"入口                |
| P1     | 作者自定义介入     | intervene 仅 accept/reject → 支持注入自由指令（如"让陈默突然翻脸"）                 | simulation.py intervene 扩展 + DirectorPanel |
| P2     | 角色 CRUD UI  | 后端 API 已有（POST/PUT/DELETE /characters）→ 前端增删改查入口                 | CharRail / CharactersPage                  |
| P2     | 世界规则可视化     | 规则注入后不可见 → 面板展示生效规则与 0-token 校验命中记录                              | DirectorPanel + world\_rules               |

> **下一阶段主线建议：「从零写一本玄幻书」端到端闭环**——主笔骨架（maestro）→ 逐场景推演（director）→ **章节成文落库（P0）** → 阅读台阅读 → 导出。P0 正文落库是该闭环的最后一公里，也是目前唯一断点。

***

## 11. 版本与提交记录（git）

| tag      | commit    | 内容                                           |
| -------- | --------- | -------------------------------------------- |
| `v1.12`  | `447d8ca` | **S3 交付**：零新表纯派生（章级张力曲线 + R1-R7 诊断 + 伏笔呼应网络 + 结构分）+ 概览「全局张力」区块（零依赖纯 SVG）+ 验收脚本 accept_s3.py（**9/9**）+ 修 B19（内存态场景判据/最新 sim 排序） |
| `v1.11`  | `6d005c4` | **S2 交付**：验收脚本 accept_s2.py（确定性 + --live，修文档债）+ 4 个归属缺陷修复 + S2 方案 §8 验收记录 + 原始输出归档 |
| —        | `af76f62` | feat(engine): S2 step5 0-token 口吻先验（style_checks 底座 + 体检接线） |
| —        | `8e1c95a` | fix(db): 对齐 ORM 与真实表结构（B18 静默漂移）+ 结构对齐守卫测试 |
| —        | `366ca59` | feat(engine): S2 step4 质检口吻风险 voice_risks（并增）      |
| —        | `6751856` | docs: 痛点清单 §5 待考虑项池（T1–T8）+ 文档债修正 + 架构总览图 |
| `v1.10`  | `e32f344` | Wiki v1.10（S2 交付 + B16/B17）+ S2 方案进度        |
| —        | `639be4b` | test: 修 DB 用例跨事件循环静默降级（B17）             |
| —        | `80373f9` | feat(engine): S2 角色口吻注入与体检回环              |
| —        | `090e98b` | docs: 新增 S2 角色口吻方案 + 痛点清单现状更正          |
| —        | `ed8873b` | fix(engine): 写手角色卡块字段对齐 CharacterCard schema（B16） |
| —        | `867df3d` | docs(wiki): §11 补记存量 8 批提交与 v1.9 / v1.9.1 tag |
| `v1.9.1` | `6547ef0` | fix: 主笔对话回退恢复两段增量下发（B15）+ Wiki 环境状态复盘 |
| `v1.9`   | `9283328` | docs: 写作痛点清单 + S1 世界状态方案 + 主笔升级设计 + Wiki v1.9 |
| —        | `c8b39d8` | style(web): 纸墨双主题样式补齐（app/characters/maestro） |
| —        | `a250cf4` | feat(web): 页面接真 + Studio 正文协作独立路由（删 PlanningPage） |
| —        | `12d1fc5` | feat(web): API/类型/hooks/组件适配（选角/正文面板/Dialog/空态） |
| —        | `6c3f5f0` | test: 正文协作/纠错循环用例 + conftest FakeLLM 提速   |
| —        | `f8ea999` | feat(api): 记账落账 + 世界状态感知 + 对话持久化 + 正文协作接口 |
| —        | `5dc14bc` | feat(engine): 纠错循环 + 正文四角色 + 世界状态 state_deltas |
| —        | `11e6e17` | feat(db): 迁移 0005-0012 + 五张新表 ORM/repo     |
| —        | `b56a3c6` | feat: 正文落库闭环（v1.2 P0：final_prose/阅读台/导出） |
| `v1.1.1` | `37a85e6` | fix: 导演台章节反查补齐（GET /chapters/{id} + repo 内存态） |
| `v1.1`   | `a643865` | feat: 涌现式小说 Agent 全链路交付（S0-S4 + 前端 v1.0 + 后端 v1.1） |
| —        | `6b73bb6` | init: 墨卷涌现式小说 Agent 项目初始化                |

> **2026-09-13 历史重写（去敏）**：`backend/app/config.py` 曾硬编码 DB 密码默认值；已用 `git filter-repo --replace-text` 将其从**全部 19 个提交与所有 git 对象**中抹除（改由 `.env` 的 `NOVEL_DB_PASSWORD` 注入，默认值留空），因此上表 hash 为**重写后**的新值，重写前的旧 hash 一律失效。

