# 场景分层全量重构计划（async SQLAlchemy 2.0 ORM + 全链路 async + book → chapter → scene → sim）

> Status: DRAFT（等待用户确认后开始执行）
> 关联 spec 目标：`.trae/specs/minimal-mvp-director-console-v02/spec.md`
> 日期：2026-08-25（v3：异步化从数据层扩展到 LLM 客户端与引擎全链路）

## 一、目标（用户原话提炼）

1. **摒弃静态数据** — 场景内容（角色卡/初始事实/导演PlanCfg）从 `.py` 文件搬进 DB，作者可编辑，真正「动态生产章节内容」。
2. **四层 id 骨架** — `book_id(小说) → chapter_id(章节) → scene_id(微场景) → sim_id(运行实例)`，三者不再混用一个字符串。
3. **数据层 ORM 化** — 引入 **SQLAlchemy 2.0 异步 ORM**（asyncpg 驱动）作为唯一数据访问层，替代手写 psycopg SQL；同期预留 pgvector 列，为未来记忆 RAG 铺路。
4. **全链路异步化** — **LLM 客户端 → 引擎 → LangGraph → 服务层 → SSE** 全部 async（`httpx.AsyncClient` + `graph.ainvoke` + async 节点），SSE 流式即为异步形态，播放时不阻塞事件循环，可支持多 sim 并发推演。
5. **导演台完整闭环** — 首页点书 → 选章节 → 选场景 → 进导演台；未结束的 sim 恢复上次工作流状态；推演期间前进/暂停/单步；举手拍板；产出可用的章节成文。
6. **角色卡感知注入与自我认知 prompt 做好** — `perceive_context` 升级为「真正读 DB 角色卡字段 + 动态现场」的注入。
7. **保留确定性回退** — 无 LLM Key 时 `decide_fn`/`converge_fn` 继续可用（闭环可测、CI 不用 Key）。

## 二、当前状态分析（探索结论）

### 后端（FastAPI + LangGraph）
- **数据模型** [models.py](file:///e:/novel_desk-agent/backend/app/schemas/models.py)：
  - `SimulationState.scenario: str`（= 场景模板 id `"betrayal_night"`）
  - `WorldState.scene_id: str`（**直接复用 scenario_name**，[service.py](file:///e:/novel_desk-agent/backend/app/services/service.py#L40)）
  - 角色卡 `CharacterCard` 已含④层可编辑字段（`system_prompt`/`think_schema`/`decide_schema`/`static_world`），只在内存里。
- **数据访问** [repo.py](file:///e:/novel_desk-agent/backend/app/data/repo.py)：手写 psycopg cursor + 原生 SQL（`simulation/events/beliefs` 三表）；DB 不可用时内存 dict 兜底。
- **会话** [session.py](file:///e:/novel_desk-agent/backend/app/data/session.py)：`psycopg_pool.ConnectionPool` 同步连接池。
- **场景注册表** [scenarios/__init__.py](file:///e:/novel_desk-agent/backend/app/scenarios/__init__.py)：`SCENARIOS = {"betrayal_night": betrayal_night}`，内容静态写死在 [betrayal_night.py](file:///e:/novel_desk-agent/backend/app/scenarios/betrayal_night.py)。
- **服务层** [service.py](file:///e:/novel_desk-agent/backend/app/services/service.py)：同步 `start/step/intervene`；`step()` 用 `graph.invoke`（**图节点只操作内存 sim，不碰 DB**，DB 仅在 save/load 边界）。
- **API** [simulation.py](file:///e:/novel_desk-agent/backend/app/api/routers/simulation.py)：`POST /sims`、`step`、`state`、`stream`(SSE，已 async 生成器)、`intervene`。
- **引擎** [graph.py](file:///e:/novel_desk-agent/backend/app/services/engine/graph.py)：单回合图；[character.py](file:///e:/novel_desk-agent/backend/app/services/engine/character.py) `perceive_context()` 已注入「性格/目标/已知beliefs/眼前环境/近况」；[director.py](file:///e:/novel_desk-agent/backend/app/services/engine/director.py) PlanCfg 来自场景文件。
- LLM 客户端 [client.py](file:///e:/novel_desk-agent/backend/app/services/llm/client.py)：同步 httpx，无 Key 返回 None → 走回退。

### 前端（Vite + React + TS）
- 路由：`/` HomePage（静态书架）· `/director` DirectorPage · `/dashboard` · `/characters`。
- [useDirectorSim.ts](file:///e:/novel_desk-agent/frontend/src/hooks/useDirectorSim.ts) 硬编码 `POST /sims?scenario=betrayal_night`；已实现 play/pause/step + SSE 消费。
- HomePage 书架是**静态假数据**（BOOKS 数组写死）。

### 测试
- [test_api.py](file:///e:/novel_desk-agent/backend/tests/test_api.py)、[test_core.py](file:///e:/novel_desk-agent/backend/tests/test_core.py)、[test_director_converge.py](file:///e:/novel_desk-agent/backend/tests/test_director_converge.py)：引擎级测试纯内存（**不碰 DB，保持同步可保留**）；API 测试用 TestClient + 假会话。

## 三、决策（已与用户确认）

| # | 决策 | 选择 |
|---|---|---|
| D1 | 场景内容建模 | **全线入库**：scene 表存初始事实/导演配置 JSON，角色卡独立表 |
| D2 | 无 Key 回退 | **保留** decide_fn/converge_fn 确定性回退（`scenarios/` 保留，仅作 fallback 源） |
| D3 | 导演台入口 | **恢复上次 + 新建两用**：优先恢复该场景「未结束 sim」，否则新建 |
| D4 | 层级深度 | 四层全建：`book → chapter → scene → sim`，DB 新增 book/chapter/scene 表 + characters 表 |
| D5 | **ORM 形态** | **异步 SQLAlchemy 2.0**（async engine + asyncpg），作为唯一数据访问层 |
| D6 | **pgvector** | **同期预留 Vector 列**（Character/Event/Fact 等模型可存 embedding），不引入新依赖，未来 RAG 直接用 |
| D7 | **全链路异步** | **LLM 客户端(httpx.AsyncClient) + 引擎(async) + LangGraph(graph.ainvoke) + 服务层(async) + SSE(async)** 统一异步；SSE 本就是 async，同步阻塞 step 是当前真正的瓶颈 |

## 四、目标架构（重构后）

### 数据层：async SQLAlchemy 2.0 ORM

```
backend/app/db/
├── engine.py       # async engine + AsyncSessionFactory（dsn 从 settings 读，quote 密码）
├── base.py         # DeclarativeBase + 公共 mixin（id/timestamps）
├── models/
│   ├── book.py     # Book: id, title, genre, status, created_at
│   ├── chapter.py  # Chapter: id, book_id FK, title, summary, order_no
│   ├── scene.py    # Scene: id, chapter_id FK, title, scenario_def,
│   │               #   initial_facts_json, plan_cfg_json, cursor_pos
│   ├── character.py# Character: id, scene_id FK, spec_json, embedding(Vector, 预留)
│   ├── simulation.py# Simulation: sim_id PK, book_id/chapter_id/scene_id FK,
│   │               #   scenario, turn, state_json, ended, created_at
│   └── memory.py   # (预留) Event/Fact 的 embedding 列（pgvector）
└── repo.py         # async Repo：save/load/…，DB 不可用时 async 内存兜底
```

### 领域层（保持结构，async 化调用点）

- `SimulationState` 新增 `book_id/chapter_id/scene_id` 字段（默认空，兼容旧快照）；`scenario` 保留为场景模板名。
- `WorldState.scene_id` 语义 = scene id（字段名不变）。
- LangGraph 图节点**改 async def**（内部仍是纯内存操作 sim），配合 `graph.ainvoke` 全链路异步。

### 关键架构决策：全链路异步（含引擎）

> 用户明确：SSE 本就该异步，同步阻塞的 step 是当前真正的瓶颈。
> 因此本次 **LLM → 引擎 → LangGraph → 服务层 → SSE 全部 async 化**：
> - **LLM 客户端**：`httpx.post`（同步）→ `httpx.AsyncClient`（async），`call_strong/call_cheap` 变 `async def`；单例 AsyncClient 复用。
> - **引擎 async 化**：
>   - [character.py](file:///e:/novel_desk-agent/backend/app/services/engine/character.py)：`think/decide` 调 llm → `async def`
>   - [director.py](file:///e:/novel_desk-agent/backend/app/services/engine/director.py)：`llm_plan/llm_converge` → `async def`（`fallback_plan` 确定性回退保持同步，`plan()` 里 await）
>   - [world.py](file:///e:/novel_desk-agent/backend/app/services/engine/world.py)：纯内存操作，可保持同步（无 IO）
> - **LangGraph**：图节点改 `async def`，调用链 `graph.invoke` → `await graph.ainvoke(...)`（LangGraph 原生支持 async 节点）
> - **服务层**：`start/step/intervene` → `async def`；`step` 内 `await repo.load → await graph.ainvoke → await repo.save`
> - **API**：全部路由 `async def`；SSE `_sim_stream` 里 `await svc.step`（不再阻塞）
>
> 收益：播放推演时不阻塞事件循环、可支持多 sim 并发、LLM 调用期间可被其他请求让路。
> 无 Key 回退路径（decide_fn/fallback_plan）不受影响，仍是纯内存同步计算。

### 场景内容加载链

1. `GET /chapters/{id}/scenes` → seed 或作者建的 scenes → 前端选择
2. `POST /sims`（body `{book_id?, chapter_id?, scene_id, resume: true}`）→ 查该 scene 下 `ended=False` 最新 sim → 有则 `resume`，无则：
   - 从 scenes 表读 `initial_facts_json`/`plan_cfg_json`
   - 从 characters 表读角色卡（含④层字段）
   - 按 `scenario_def` 从 `app/scenarios/` 加载 `decide_fn/converge_fn` 作为无 Key 回退
   - 组装 `SimulationState`（带三个 id）→ save → 返回 `{sim_id, resumed: false}`

### 角色持久化与 N+1 防线（架构约束，硬性）

- **角色卡是"一等实体"**：独立 `characters` 表（`scene_id` 关联、作者可编辑），而非藏在 sim 快照里。
- **装配时拷贝快照**：`POST /sims` 建 sim 时从 `characters` 表按 `scene_id` 批量读一次（O(1)，带④层 prompt），拷贝进 `SimulationState.characters`。**此后运行时（每回合 step/SSE）绝不回查 `characters` 表**——sim 是历史快照，作者改角色只影响新回合。
- **杜绝运行时 N+1**：运行时每次 step 仅 load + save 各 1 次 `state_json`，与角色数/回合数无关（O(1) 而非 O(n)）。
- **前端导航防 N+1**：书架→章节→场景→角色是链式导航，禁止前端用 N 个请求逐层拉取；提供**聚合端点**一次返回「书→章→场景（含角色）」树（见阶段 B4 / D1）。

## 五、分阶段实施方案

> 按「数据层 → 服务/API → 引擎注入 → 前端 → 验证」推进，每阶段可独立验证（pytest/tsc）。**循序渐进**：每阶段完成即验证，不堆积。

### 阶段 A：数据层（async SQLAlchemy 2.0 + 建表 + seed）

**A1. 依赖**
- `pyproject.toml` 新增：`sqlalchemy[asyncio]>=2.0`、`asyncpg`、`pgvector`（SQLAlchemy 集成）。
- 迁移到 uv 环境：`uv sync`（backend 目录）。

**A1b. `SimulationState` 字段调整（含修复 `last_main_actor` 跨回合失效）**
- 新增 `book_id/chapter_id/scene_id`（默认空，兼容旧快照）。
- 新增 `last_main_actor: str = ""`（上层字段）——修复当前「每回合 `graph.invoke` 重建 state → 主戏角色记忆丢失」的缺陷：`step()` 把 `sim.last_main_actor` 读回传给 `graph.invoke`，回合结束把 `character_phase` 返回的主戏角色写回 `sim` 并随 `state_json` 落库，使「主动权轮换」真正跨回合生效。

**A2. ORM 模型**（新目录 `backend/app/db/`，如上）
- `engine.py`：`create_async_engine(settings.dsn_async)`；dsn 用 `postgresql+asyncpg://...`；`async_sessionmaker`。
- `base.py`：`class Base(DeclarativeBase)`。
- 五个模型 + pgvector 预留列（`Mapped[list[float]] = mapped_column(Vector(1536), nullable=True)` 等，不计算向量，仅铺地基）。

**A3. async Repo 替换手写 SQL**
- `Repo` 改为 async：`async def save/load/…`，内部用 `AsyncSession`；`create_all()` 建表。
- 内存兜底改 async 版本（dict + `asyncio.Lock`），保证无 DB 仍可跑（沿用现有降级思路）。

**A4. session.py 改造**
- `get_db` 依赖改为产出 `AsyncSession`（或 service 内部管理 session，API 只注 Repo）。

**A5. seed 数据**
- 新增 `backend/app/db/seed.py`：把 `betrayal_night()` 静态内容灌成 books/chapters/scenes/characters 记录（幂等：表空才 seed）。运行入口：`uv run python -m app.db.seed`。

**A6. 迁移旧数据**
- `simulation` 旧行无新列：ORM 模型字段给默认值可 null → 旧 snapshot `state_json` 里已含 `SimulationState` 新字段默认值，向后兼容（AC-9）。

### 阶段 B：服务层 / API 异步化 + 四层接口

**B1. LLM 客户端 async 化** [client.py](file:///e:/novel_desk-agent/backend/app/services/llm/client.py)
- `httpx.post`（同步）→ 模块级 `httpx.AsyncClient`；`_chat/call_strong/call_cheap` 改 `async def`。
- 无 Key / 调用失败仍返回 None（调用方走确定性回退），错误语义不变。
- 注意 `available` 保持属性；LLM 客户端单例复用 AsyncClient，应用关闭时需关闭（lifespan 挂 `await client.aclose()`）。

**B2. 引擎 async 化** [character.py](file:///e:/novel_desk-agent/backend/app/services/engine/character.py) / [director.py](file:///e:/novel_desk-agent/backend/app/services/engine/director.py) / [graph.py](file:///e:/novel_desk-agent/backend/app/services/engine/graph.py)
- `CharacterEngine.think/decide`：`await llm.call_strong(...)` → `async def`。
- `DirectorEngine.plan`：`await llm_client.call_cheap(...)`（llm_plan/llm_converge async）；`fallback_plan` 确定性路径保持同步。
- [graph.py](file:///e:/novel_desk-agent/backend/app/services/engine/graph.py)：`director_plan/character_phase/...` 节点改 `async def`；`build_graph` 返回编译图不变；服务层用 `await graph.ainvoke({...})`。
- `world.py` 保持同步（纯内存，无 IO）。
- **测试影响**：test_core 里 `monkeypatch.setattr(llm_mod.client, "call_cheap", fake_call_cheap)` 需适配为 `async def`（pytest 需 `async def` 测试或 `asyncio.run` 包装）。

**B3. Service 层 async 化** [service.py](file:///e:/novel_desk-agent/backend/app/services/service.py)
- 全改 `async def`：`start`（入参 book/chapter/scene_id）、`step`（`await repo.load` → `await graph.ainvoke` → `await repo.save`）、`resume(scene_id)`、`intervene`。
- 新增 `list_books/list_chapters/list_scenes/get_scene_characters`。

**B4. API 路由 async 化 + 四层接口** [simulation.py](file:///e:/novel_desk-agent/backend/app/api/routers/simulation.py)
- `POST /sims` body `{book_id?, chapter_id?, scene_id, resume?: bool}` → `{sim_id, resumed}`。
- 新增 `GET /books`、`GET /books/{id}/chapters`、`GET /chapters/{id}/scenes`、`GET /scenes/{id}`（含 characters）。
- **聚合端点 `GET /books/{id}/tree`**：一次返回「书 → 章 → 场景（含角色）」树（内部 1-2 次查询，内存组装），供前端书架→章节→场景一次拉完，杜绝客户端 N+1。
- `stream`/`state`/`step`/`intervene` 改 `async def` + `await svc.*`；`_sim_stream` 内 `await svc.step`（不再阻塞事件循环）。

**B5. run_demo.py 适配**
- 演示脚本包一层 `asyncio.run(main())`；或提供「直接用内存 sim 跑图」的无 DB 模式（推荐后者，run_demo 本来就不落库）。

### 阶段 C：感知注入与自我认知 Prompt（角色引擎）

**C1. [character.py](file:///e:/novel_desk-agent/backend/app/services/engine/character.py) `perceive_context()` 增强**
- 感知注入（信息差来源）：环境事实（按 visible_to 裁剪）✅已有；动态现场（facts 每回合最新成立项）✅增强；近况事件 ✅已有。
- 自我认知注入（④层角色卡字段）：`static_world` → 「【世界观设定】」；`system_prompt` → 作者腔调/决策偏好指令；`think_schema`/`decide_schema` → JSON 约束（已有默认兜底）。
- 保证：不读全量黑板，只读该角色子集（视角隔离铁律）。

**C2. `_compose_prompt` 顺序校对**
- 确认 `system_prompt → static_world → context → 要求` 与 schema 模板语义对齐，不改引擎结构。

### 阶段 D：前端入口（书架 → 章节 → 场景）

**D1. 新增 API 层** `frontend/src/api/novel.ts`
- `fetchBookTree(bookId)` → `GET /books/{id}/tree`（一次拉全书→章→场景→角色，防客户端 N+1）
- `listBooks()` → `GET /books`（书架概览）
- `getScene(sceneId)`、`createOrResumeSim({book_id, scene_id})` → `POST /sims`

**D2. HomePage 改造** [HomePage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/HomePage.tsx)
- 书架拉真数据（替换静态 BOOKS）；点书 → 章节选择视图（新组件 `ChapterScenePicker`）→ 选章节 → 选场景 → 进导演台 `/director/:sceneId`。
- 「快速入口-导演台」→ 默认/最近场景。

**D3. 路由** [App.tsx](file:///e:/novel_desk-agent/frontend/src/App.tsx)：新增 `/director/:sceneId`；`/director` 无参回退默认场景（不破坏旧链接）。

### 阶段 E：前端导演台（恢复 + 播放控制完善）

**E1. [useDirectorSim.ts](file:///e:/novel_desk-agent/frontend/src/hooks/useDirectorSim.ts)**
- 入参 `sceneId`；`createOrResumeSim` → `{sim_id, resumed}`；`resumed=true` 时先拉 state 显示已推进内容，再接管 play/pause/step。
- 确认 resume 与现有 SSE/举手/单步联动。

**E2. 角色页** [CharactersPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/CharactersPage.tsx)
- 角色列表从 `GET /scenes/{id}/characters` 拉取（替换静态 CHAR_LIST）。
- ④ 层 prompt 的前端编辑提交（`PUT /characters/{id}`）**本次后置（P2）**，仅展示。

### 阶段 F：测试与验证

**F1. 测试适配**
- 引擎级测试（[test_core.py](file:///e:/novel_desk-agent/backend/tests/test_core.py)、[test_director_converge.py](file:///e:/novel_desk-agent/backend/tests/test_director_converge.py)）：**async 化**——`test_think_decide_fallback_without_key` 等调 `think/decide` 的用例改 `pytest.mark.asyncio`（或 `asyncio.run` 包装）；`monkeypatch` 的 fake call 函数改 `async def`；`SimulationState` 新字段默认值兼容。
- API 测试（[test_api.py](file:///e:/novel_desk-agent/backend/tests/test_api.py)）：TestClient 走 async 路由（`httpx.AsyncClient` + ASGITransport 或 TestClient 自动支持）；`step` 断言改 `sim["turn"]`；`POST /sims` 改 body；新增 `GET /books`、resume 冒烟。
- 新增 `test_db_orm.py`：建表 → seed → ORM 增删改查 → pgvector 列存在（可走真实 DB，或 skip_if_no_db）。
- 新增 `test_scene_hierarchy.py`（可内存态）：seed → `GET /books` → 按 scene 建 sim → step → resume 返回同一 sim。

**F2. 端到端验证**
- `uv run pytest`（backend）全绿；`npx tsc --noEmit`（frontend）零错误。
- 浏览器实测：真实书架 → 点书 → 章节 → 场景 → 导演台；播放推回合；刷新恢复上次回合；举手拍板继续。
- 并发冒烟：同时（或先后快速）开两个 sim 流，确认播放互不阻塞（事件循环不再被 LLM 同步阻塞）。

## 六、验收标准（AC，二值可验证）

- AC-1 `GET /books` 返回 seed 书籍（「雨夜书房」等），非空。
- AC-2 `POST /sims` body 带 `scene_id` → `{sim_id, resumed: false}`，`state.turn == 0`。
- AC-3 **断线恢复**：建 sim → step 3 回合约 → 同 scene_id `POST /sims`(resume=true) → 返回原 sim_id，`state.turn == 3`。
- AC-4 角色卡 ④ 层字段（`system_prompt`/`static_world`）从 characters 表读取，`perceive_context` 输出含该角色 `static_world`，不含他人视角。
- AC-5 无 LLM Key 时 `decide_fn` 回退仍生效（`test_think_decide_fallback_without_key` 通过）。
- AC-6 前端：首页 → 书 → 章节 → 场景 → `/director/:sceneId` 正常渲染；播放/暂停/单步可操作。
- AC-7 刷新 `/director/:sceneId` 后恢复上次回合（时间线/黑板有内容），不新建空 sim。
- AC-8 `uv run pytest` 全绿；`npx tsc --noEmit` 零错误。
- AC-9 旧数据兼容：原 `simulation` 行（无新列）仍可 `load`，不报错。
- AC-10 **ORM 可用**：`test_db_orm.py`（有 DB 时）验证 Book/Chapter/Scene/Character/Simulation 五模型 CRUD + pgvector 列存在。
- AC-11 **全链路异步**：`llm.call_strong/call_cheap` 是 `async`；`graph.ainvoke` 被服务层调用；SSE 推演期间 `GET /health` 或另一请求能被响应（不阻塞事件循环）。
- AC-12 **LLM 异步回退不变**：mock async `call_cheap` 返回 None → `plan()` 回退 `fallback_plan`（test_core `test_director_llm_plan_none_falls_back` async 化后通过）。
- AC-13 **N+1 防线**：`GET /books/{id}/tree` 一次返回书→章→场景（含角色）完整树；运行时 `step` 期间 `characters` 表 0 次回查（可埋点/日志断言）；前端无逐层列表请求。

## 七、风险与边界

| 风险 | 应对 |
|---|---|
| 全链路 async 化侵入引擎 | 分步推进：LLM client → 引擎 → graph → service → API，每层改完即跑对应测试；`world.py` 等纯内存保持同步，最小化异步扩散 |
| 引擎测试大量 monkeypatch 需适配 | 引擎 fake call（`call_strong/call_cheap`）统一改成 `async def`；pytest 加 `pytest-asyncio` 或统一 `asyncio.run` 包装；先改测试再改实现（保持红绿可追踪） |
| LangGraph async 节点兼容性 | LangGraph 原生支持 `async def` 节点 + `await graph.ainvoke(...)`；如遇版本问题锁 `langgraph>=某版本` 或回退为同步 invoke（仅 LLM 调用用 `asyncio.to_thread` 桥接，保真语义） |
| asyncpg + 本地镜像兼容性 | 项目规范要求 pgvector 0.8.6 镜像（rag-kb-postgres:pg16），asyncpg 连接 pg16 无版本障碍；如遇兼容问题退回 psycopg3 async 驱动（SQLAlchemy 2.0 双驱动支持） |
| DB 迁移破坏旧数据 | ORM 模型新字段可 null + state_json 快照含默认值，向后兼容；加列不删旧列 |
| 前端路由改动影响现有导航 | `/director` 保留无参可访问（默认场景），新增 `/director/:sceneId` |
| seed 与静态文件漂移 | seed 由 `betrayal_night()` 工厂生成，单一来源 |
| 一次性改动过大 | 按 A→F 分阶段，每阶段 pytest/tsc 验证后再进下一阶段 |

## 八、明确不做（Out of scope）

- 不建 Book/Chapter 的前端 CRUD 编辑页（本计划只做导航 + 内容入库；增删改后续）
- 不做角色卡 ④ 层 prompt 的前端编辑提交（`PUT /characters` 后置 P2）
- 不做记忆 RAG 的实际计算（只预留 pgvector 列）
- 不引入 Redis（现有 Postgres 足够）

## 九、执行顺序（loop，循序渐进）

1. 阶段 A：async SQLAlchemy 2.0 数据层（模型 + repo + session + seed + 迁移）→ 验证：`python -m app.db.seed` 可跑、`test_db_orm` 过
2. 阶段 B1+B2：LLM 客户端 async + 引擎 async + graph.ainvoke → 验证：test_core 引擎用例（synchronize 后全绿）
3. 阶段 B3+B4：Service / API async 化 + 四层接口 → 验证：pytest test_api
4. 阶段 C：感知注入/自我认知 prompt → 验证：test_core 相关用例
5. 阶段 D：前端入口（书架→章节→场景）→ 验证：tsc + 浏览器
6. 阶段 E：前端导演台恢复 → 验证：浏览器断线恢复
7. 阶段 F：全量验证（含并发冒烟）→ 修正 → 回归，直至 AC 全绿

每阶段完成即跑对应验证命令，发现问题回滚修复再进下一阶段。