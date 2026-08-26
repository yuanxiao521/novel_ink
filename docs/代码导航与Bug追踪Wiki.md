# 墨卷 · 涌现式小说 Agent —— 项目协作 Wiki

> **定位**：本项目长期协作文档（唯一导航源）。一张图看懂全部源码与目录，快速定位"某个功能/报错应该看哪个文件"，并对齐 S0–S4 之后的前端演进与后端补齐进度。
> **维护纪律**：每次新增/重构模块、修 bug、改架构后，**必须同步更新**本文件（含版本历史与 Bug 追踪表）。此文档作为团队持续协作的基准。

---

## 0. 版本历史

| 版本 | 日期 | 变更摘要 | 作者 |
|---|---|---|---|
| **v1.2** | 2026-08-26 | ★正文落库闭环（P0）：`scenes.final_prose`（迁移 0004）+ 手动定稿 `POST /sims/{id}/finalize` + 场景/章正文查询 + 全书 md 导出；阅读台 ReaderView 接真（按章渲染+翻章+导出）；repo 内存态列表查询补齐；测试 test_api 5→8 用例（全套 35 用例全绿） | 主笔 Agent |
| **v1.1.1** | 2026-08-26 | 导演台反查修复：新增 `GET /chapters/{id}`（路由+service+repo 内存态），回归测试 `test_chapter_detail_lookup`（44 passed）；tag `v1.1.1` | 主笔 Agent |
| **v1.1** | 2026-08-26 | ★后端补齐：灵感池接口（CRUD+主笔生成+采纳持久化）、主笔共创对话 SSE、灵感→骨架落地、测试补强（43 passed） | 主笔 Agent |
| **v1.0** | 2026-08-26 | ★前端里程碑：主笔共创工作台（maestro）React 化融合、纸墨双主题统一、导航改版；wiki 体系化成立（版本历史+变更日志） | 主笔 Agent + 作者 |
| v0.2 | 2026-08-26（早） | S4 玄幻闭环：世界规则 0-token 校验、成文升级、换场续场、伏笔三态 | 主笔 Agent |
| v0.1 | 2026-08-26（初始） | 初版导航文档（S0–S4 后基线） | 主笔 Agent |

> 版本规则：功能交付/架构变更 → 升版本；纯 bug 修复 → 只记 Bug 追踪表不下版本。

---

## 1. 项目定位与数据模型

四层目录体系，从"书"一直下钻到"推演回合"：

```
Book(书) → Chapter(章) → Scene(场景=一台戏) → Simulation(推演实例,每场景一个)
                                        → Event(回合事件,挂 sim 下)
                                        → Foreshadow(伏笔,跨场景生命周期)
                                        → WorldRule(世界观规则,玄幻硬约束)
```

| 层 | 载体 | 关键文件 |
|---|---|---|
| 书 | `books` 表 | [book.py](file:///e:/novel_desk-agent/backend/app/db/models/book.py) |
| 章 | `chapters` 表 | [chapter.py](file:///e:/novel_desk-agent/backend/app/db/models/chapter.py) |
| 场景 | `scenes` 表 | [scene.py](file:///e:/novel_desk-agent/backend/app/db/models/scene.py) |
| 角色 | `characters` 表 | [character.py](file:///e:/novel_desk-agent/backend/app/db/models/character.py) |
| 推演 | `simulations` 表 + `sim` 内存黑板 | [simulation.py](file:///e:/novel_desk-agent/backend/app/db/models/simulation.py) |
| 伏笔 | `foreshadows` 表 | [foreshadow.py](file:///e:/novel_desk-agent/backend/app/db/models/foreshadow.py) |

---

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
│   │   │   │   └── world_rules.py     # 世界观规则 0-token 校验器(S4)
│   │   │   └── llm/client.py   # LLM 客户端（DeepSeek，强/廉模型，流式）
│   │   ├── db/
│   │   │   ├── repo.py         # 数据访问（ORM/内存态双模式）
│   │   │   ├── models/         # 6 张 ORM 表
│   │   │   ├── engine.py       # async engine（pgvector）
│   │   │   └── seed.py         # 种子数据
│   │   ├── schemas/models.py   # 领域模型（SimulationState/WorldState…）
│   │   ├── scenarios/          # 场景剧本模板（betrayal_night…）
│   │   └── errors.py           # 领域异常 → HTTP
│   ├── alembic/versions/       # 迁移：0001 基线 / 0002 新列+伏笔表
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

---

## 3. 运行时拓扑

```
浏览器 (http://127.0.0.1:5173)  ── CORS ──>  uvicorn (127.0.0.1:8000)
  │  ├─ REST  /api/v1/*                FastAPI app.main:app
  │  └─ SSE   /api/v1/sims/{id}/stream ──> Graph (单回合循环)
                                              │  LLM: DeepSeek(强=演绎/廉=导演·成文)
                                              ↓
                          PG16+pgvector  novel-ink-db (5433, 卷 novel-ink-data)
```

- 前端固定连 `http://127.0.0.1:8000`（[types.ts](file:///e:/novel_desk-agent/frontend/src/types/types.ts) `API_BASE`，避免 localhost→IPv6 坑）
- 后端 CORS 白名单：8000/5173 两个 origin（[main.py](file:///e:/novel_desk-agent/backend/app/main.py#L52-L54)）
- DB 未就绪时 `persist` 走内存态兜底，测试/演示不依赖容器

---

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

### 4.2 单回合节点链（graph.build_graph，5 节点）

```
director_plan ─> _apply_guidance(曝光→信念/调权/注入) ─> character_phase
   ─> refresh_world(冲突回合) ─> render_prose(180-280字旁白)
```

- 角色串行决策（保证行动事件先于另一角色感知）
- 护栏 `_guard_and_record` 四层：人设 → 世界事实 → **(S4) 世界观规则** → 熔断降级
- 成文 stream_cheap_text() token 级流式（字符实时打字效果）

### 4.3 收束 / 换场（场景切下一场景）

```
导演收敛判定(≥6回合 → LLM 两次同结局)
→ analyze_scene_close: 因果补全(caused_by/causal_pressure) + 伏笔三态推进 + 场景摘要 + 下一场提示
→ next_scene_seed → SSE done 带 next_scene
→ 前端自动建新 sim 续场（useDirectorSim 内）
```

### 4.4 主笔规划（书骨架 → ★工作台入口）

```
[POST] /books/{id}/plan        -> chief_planner 生成 worldview+chapters+foreshadow（LLM，约 60-90s）
[POST] /books/{id}/plan/commit -> 落库（章/场景/伏笔/规则；★幂等：按标题命中已存在则跳过不重复）
```

> v1.0 前端工作台："让主笔构思"真实调 LLM 约 60-90s（deepseek 生成长骨架），**前端的 ideating 状态必须在成功/失败分支都复位**（B10）。
> v1.1 已接真：灵感池（列表/生成/采纳）与主笔共创对话（SSE）走后端 API，mock 仅作 API 不可用时的回退。

### 4.5 导演台书树反查（scene → chapter → book，v1.1.1 补齐）

```
DirectorPage 挂载 → 优先取路由 state 的 book_id（从书架/规划页进入时携带）
  无则：GET /scenes/{id} → chapter_id → GET /chapters/{id} → book_id   ← B11 修复点（曾缺端点 405）
  → GET /books/{id}/tree → 顶栏书标题 + 场景切换下拉（换场景 = 换路由重挂新 sim）
```

---

## 5. 前端模块地图（★ v1.0 更新）

| 路由 | 组件 | 职责 |
|---|---|---|
| `/` | HomePage | 书架 → 选书 → 场景 → 进导演台 |
| `/dashboard` | DashboardPage | 概览工作台（静态样表） |
| `/director/:sceneId?` | DirectorPage | ★ 导演台：组装 5 组件 |
| `/maestro` | ★MaestroPage | ★主笔共创工作台：三栏（灵感池+骨架树+共创对话）+ 底部张力曲线 |
| `/planning` | PlanningPage | 规划页（四层树 CRUD + 主笔规划）；★工作台"完整编辑"跳这里 |
| `/characters` | CharactersPage | 人物页（静态样表） |

**侧边导航**（[Sidebar.tsx](file:///e:/novel_desk-agent/frontend/src/components/backoffice/Sidebar.tsx)）：概览 → **☆主笔创作(/maestro)** → 设定 → 人物 → 大纲 → 伏笔 → 章节 → 记忆包 → 体检。新增"主笔创作"替换原"规划"。

**MaestroPage 内部结构**（[pages/MaestroPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/MaestroPage.tsx)）：

| 区域 | 内容 | 数据来源 |
|---|---|---|
| 顶栏 | 方向输入 + 展开面板 + 「让主笔构思」+ agent 状态 | plan API / mock 回退 |
| 左栏 | 灵感池（灵感卡⇄已采纳） | ★真实 /books/{id}/inspirations（列表/generate/采纳 PATCH，v1.1） |
| 中栏 | 书籍骨架树（章/场景，可展开折叠） | ★真实 /books/{id}/tree |
| 右栏 | 主笔共创对话（骨架预览开关/工具角标） | ★真实 /books/{id}/chief/chat SSE（v1.1） |
| 底部 | 全书张力曲线（canvas，★主题跟随 MutationObserver 重绘） | 本地静态数据 |

**导演台组件拆分**（[components/director](file:///e:/novel_desk-agent/frontend/src/components/director)）：

| 组件 | 职责 | 数据来源 |
|---|---|---|
| CharRail | 角色卡（情绪条/信念/think/act） | SSE character_* |
| CenterStage | 中央舞台（黑板 events / 成文 prose / 收束汇报卡） | SSE |
| DirectorPanel | 导演提示 + 举手卡（同意/拒绝） | SSE director_* |
| TimelineBar | 底部回合时间线 + 播放/单步/**回退按钮** | sim state + archives |
| ReaderView | 沉浸阅读视图 | 成文 |

前端状态通道：
- `useDirectorSim`（[hooks](file:///e:/novel_desk-agent/frontend/src/hooks/useDirectorSim.ts)）：SSE 全量分派 + 自动续场 + 回退归档
- `useDirectorChat`：作者↔导演共创对话（token 级流式）

**样式体系（★纸墨双主题已全站统一）**：
- [tokens.css](file:///e:/novel_desk-agent/frontend/src/styles/tokens.css)：`<html data-theme="ink|paper">` 驱动全部 CSS 变量
- [maestro.css](file:///e:/novel_desk-agent/frontend/src/styles/maestro.css)：`--maestro-*` 变量同样挂 `[data-theme]` 双套（墨深/纸浅），顶栏渐变、badge、卡片底色全用主题变量；canvas 曲线从 CSS 变量读色
- 切换入口：各页顶栏 `ThemeToggle`（☯ 墨/纸）

---

## 6. 测试地图（backend/tests）

| 文件 | 覆盖 | 跑法 |
|---|---|---|
| test_core.py | 世界黑板/护栏/推理核心（内存态） | 快速 |
| test_api.py | API 冒烟 + 启动→step→状态闭环（TestClient+内存态） | 快速 |
| test_db_orm.py | 真实 PG：基线表/级联/pgvector 兼容 | **DB 不可用自动 skip** |
| test_director_converge.py | 导演回合护栏（<3 不收束、LLM 收敛判定） | 快速 |
| test_scene_hierarchy.py | 章→场景层级 | 快速 |
| test_world_rules.py | 世界观规则 0-token 校验（negation/exclusive） | 快速 |

> 2026-08-26 v1.1.1 基线：**44 passed, 6 skipped**（6 个 skipped = test_db_orm 需 docker；test_api 含章节反查回归 `test_chapter_detail_lookup`）。
> 全量测试约 6 分钟（部分用例含 sleep），快速迭代可只跑 `pytest -q tests/test_core.py tests/test_world_rules.py`.
> 前端检查：`cd frontend && npx tsc --noEmit`（当前零错误）。

---

## 7. Bug 追踪表（当前已知问题 / 已修复记录）

| # | 状态 | 问题 | 根因/证据 | 修复位置 |
|---|---|---|---|---|
| B11 | ✅ 已修 | 无 book_id 直入导演台（URL/刷新）时书标题/树缺失 | 后端未暴露 `GET /chapters/{id}`（405）；repo.get_chapter 内存态缺 `_mem` 分支 | [simulation.py](file:///e:/novel_desk-agent/backend/app/api/routers/simulation.py#L133-L140) 新增路由 + [repo.py](file:///e:/novel_desk-agent/backend/app/db/repo.py) 内存态兜底；回归测试 `test_chapter_detail_lookup`（v1.1.1） |
| B10 | ✅ 已修 | maestro 工作台「让主笔构思」结束后按钮卡在"主笔构思中…" disabled | 真实 plan API 成功分支漏 `setIdeating(false)`（只有演示回退分支复位） | [MaestroPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/MaestroPage.tsx#L262-L288) 两分支都复位 |
| B9 | ✅ 已修 | maestro 固定深色，不随纸墨主题切换 | `--maestro-*` 挂在 `:root` 硬编码墨色 | 拆成 `[data-theme="ink"/"paper"]` 双套 + canvas 读 CSS 变量 + MutationObserver 重绘 |
| B8 | ✅ 已修 | webapp/ 静态版被错误融合 maestro（应融合 React 版） | 消息歧义：目标文件是 webapp 三件套，真意图是 React frontend | React 建 MaestroPage/style + 路由 + 导航；webapp 保留 .bak 备份 |
| B7 | ✅ 已修 | 规划页点"plan"右侧空白 | `.app-shell flex-direction: column` 导致 sidebar 与 main-col 上下堆叠，内容区高度=0 | [app.css#L31](file:///e:/novel_desk-agent/frontend/src/styles/app.css#L31) 改 `row` |
| B6 | ✅ 已修 | `.venv\Scripts\python.exe` 报 0xC0000135 (DLL 缺失)，uv/pytest 全挂 | venv 启动器损坏，缺 python312.dll | 用 uv 缓存 base python.exe + 拷 python312.dll/vcruntime140 覆盖（已备份 `.bak`） |
| B5 | ✅ 已修 | 收敛后无续场，只 3 回合没下文 | 场景无 next_scene 机制 | S3.2：analyze_scene_close + SSE done 带 next_scene + 前端自动续场 |
| B4 | ✅ 已修 | 真实 LLM 回合 1 提前收束 | 收敛判定缺最低回合护栏 | test_director_converge：`turn<3 → 不收束` |
| B3 | ✅ 已修 | 历史 sim 死锁（ended=true+converged=false 挡恢复） | resume 未排除 ended | resume_latest 排除 ended=true + 清库 |
| B2 | ✅ 已修 | SSE 回合级整批输出，无实时感 | graph 用默认 stream_mode | 改 `astream(custom)` + `get_stream_writer()` 事件级 |
| B1 | ✅ 已修 | 从 backend/ 启动读不到根 .env | config env_file 相对路径 | 改绝对路径指向根 `.env` |

**当前环境状态（2026-08-26 收盘）**：后端 8000 **运行中**、数据库容器 5433 **运行中**、前端 vite **运行中**（端口占用重启需先清 PID）。

### 7.1 排查 bug 的标准动作

1. 看日志：`backend/logs/app.log`（业务 INFO 全量，5MB×3 轮转），比终端全
2. 确认服务三连：`netstat -ano | findstr ":5433"` / `:8000` / `:5173`
3. 跑最小测试集：`cd backend && .venv\Scripts\python.exe -m pytest -q test_core.py test_world_rules.py`
4. 前端类型：`cd frontend && npx tsc --noEmit`
5. 项目已建 git（v1.1 起）：改动及时提交，大改前确认 `git status` 工作区干净

---

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

**LLM 可选**：根目录 `.env` 未配 `NOVEL_OPENAI_BASE_URL/KEY` 时走确定性回退（不需要 key 也能跑通闭环）；配了则用 `NOVEL_MODEL_STRONG`（角色演绎）+ `NOVEL_MODEL_CHEAP`（导演/成文/主笔），项目约定 DeepSeek `deepseek-v4-pro` / `deepseek-v4-flash`。

---

## 9. 改代码铁律（浓缩自项目记忆，长期有效）

- **版本管理**：项目已建 git（v1.1 起，仓库身份 `lvco`）；改动及时提交、里程碑打 tag（当前 `v1.1.1`），大改前确认工作区干净。历史教训：无 git 时期覆盖 index.html 不可逆丢失
- **venv 规范**：一律用 `.venv`，不混系统 Python（系统 python 缺 pgvector 等依赖）
- **测试规范**：pytest 默认 FakeLLM（无网络无消耗）；真 LLM 需 `--live`
- **DB 约束**：容器必须 `rag-kb-postgres:pg16` + 卷 `novel_ink-data`；密码含 `@` 用 `quote()` 编码
- **架构约定**：四层 prompt 分层（书籍/导演/环境感知/角色卡）；角色最终 Prompt = ③环境 + ④角色卡 + ②导演相关信念
- **主题规范**：新增面板样式必须走 `[data-theme]` CSS 变量（tokens.css 体系），禁止硬编码色值
- **文档为准**：`docs/prompt核心设定.md`（和 MVP设计.md 平级）是规范依据；本 Wiki 是导航与协作基准

---

## 10. 后续路线（v1.1.1 盘点后 · 下一阶段）

**已交付（原 P0 全部完成，v1.1）**：灵感池接口（CRUD + 主笔 generate + 采纳持久化）、主笔共创对话 SSE、灵感→骨架落地（commit 引用已采纳灵感）、前端 MaestroPage 全部接真。

**导演台全流程对齐盘点（2026-08-26 v1.1.1）**：启动/恢复（四层装配）、SSE 事件级流、播放/暂停/单步、举手同意/拒绝、导演对话（token 流式）、时间线查看/回退、收束汇报（director_close）、自动续场（next_scene）、书树反查 + 场景切换 —— **前后端已全部对齐，无缺端点**。

剩余缺口（按"推演 → 成书"闭环排序）：

| 优先级 | 项 | 现状 → 目标 | 涉及文件 |
|---|---|---|---|
| **P0** | **正文落库/导出** | prose 仅内存展示，收束/换场后不持久化 → 场景收束时定稿正文落库（章节维度聚合），支持导出 markdown | service（收束钩子）+ repo + 新迁移/字段 + 新端点 |
| P1 | 阅读台接真 | ReaderView 纯静态 → 渲染已落库章节正文 + 翻章 | ReaderView.tsx + 新查询端点 |
| P1 | 伏笔追踪面板 | 仅收束汇报文字提及 → 独立伏笔面板（三态时间线，`GET /books/{id}/foreshadows` 已有，前端未消费） | 新组件 + DirectorPage/侧边"伏笔"入口 |
| P1 | 作者自定义介入 | intervene 仅 accept/reject → 支持注入自由指令（如"让陈默突然翻脸"） | simulation.py intervene 扩展 + DirectorPanel |
| P2 | 角色 CRUD UI | 后端 API 已有（POST/PUT/DELETE /characters）→ 前端增删改查入口 | CharRail / CharactersPage |
| P2 | 世界规则可视化 | 规则注入后不可见 → 面板展示生效规则与 0-token 校验命中记录 | DirectorPanel + world_rules |

> **下一阶段主线建议：「从零写一本玄幻书」端到端闭环**——主笔骨架（maestro）→ 逐场景推演（director）→ **章节成文落库（P0）** → 阅读台阅读 → 导出。P0 正文落库是该闭环的最后一公里，也是目前唯一断点。

---

## 11. 版本与提交记录（git）

| tag | commit | 内容 |
|---|---|---|
| `v1.1.1` | `0544f0c` | 导演台章节反查补齐（GET /chapters/{id} + repo 内存态） |
| `v1.1` | `f1e0e10` | 涌现式小说 Agent 全链路交付（S0-S4 + 前端 v1.0 + 后端 v1.1） |
| — | `4eda365` | 项目初始化 |