# 后端补齐：灵感池接口 + 主笔共创对话 + 灵感落地 + CRUD 测试 Implementation Plan

> Status: APPROVED
> Source: 用户请求（Wiki §10 后续路线，P0/P1 全选，按优先级逐步实施）
> Mode: --deliberate（涉及数据迁移+跨前后端接口契约+编排链路完整）
> Iterations: 2 / 3
> Author: 主笔 Agent（lvco 项目）
> Last updated: 2026-08-26

## Requirements summary

前端 v1.0（主笔共创工作台）已就位，但灵感池与共创对话仍是 mock。本 plan 补齐后端使其成为**真实链路**：
1. **P0-1 灵感池接口**：灵感卡 CRUD + 主笔生成（工具调用）+ 采纳状态持久化（新表 `inspiration_cards`）
2. **P0-2 主笔共创对话**：SSE 流式 chat，注入书骨架上下文（复用 `stream_cheap_text`）
3. **P1-1 灵感→骨架落地**：采纳的灵感卡注入主笔规划（plan 阶段作为 context）
4. **P1-2 CRUD 测试补强**：新接口单测 + 内存态全覆盖，字段严格对齐前端 TS 类型

用户硬性要求：**接口输入输出对齐、编排链路完整、字段小问题不能出、按优先级逐步做**——本 plan 是"再做一遍的定盘"，不追求表面 demo。

---

## Acceptance criteria

- AC-1 `inspiration_cards` 表 + 0003 迁移可跑，Repo 提供 list/save/delete/update_adopted，与 Foreshadow 同构（id/book_id/type/text/… + TimestampMixin）
- AC-2 POST /books/{id}/inspirations（作者自建）与 POST /books/{id}/inspirations/generate（主笔 LLM 生成，fallback 确定性模板）均落库并返回标准 Schema（icon/title/desc/type/source/adopted）
- AC-3 PATCH /inspirations/{id} 只改 adopted 字段（采纳⇄取消），DELETE 按 id 删除；接口字段与前端 `InspCard` TS 类型一一对应（无多余/缺失字段）
- AC-4 POST /books/{id}/chief/chat 以 SSE `event: token / data: {delta}` 流式返回，上下文 = 书骨架摘要（title/synopsis/章节标题列表）；无 LLM 时回退确定性文案并正常结束
- AC-5 chief_planner.plan_and_generate 接受 `inspiration_ids` 参数，把已采纳灵感作为方向 context 注入 plan prompt；commit_book_plan 幂等逻辑不受影响（回归通过）
- AC-6 新增测试：inspiration CRUD / generate fallback / chief chat SSE / plan+inspiration 集成，全部 FakeLLM 可跑；全量 `pytest -q` ≥ 34 passed，`tsc --noEmit` 零错误
- AC-7 前端 MaestroPage 灵感池与对话接真实 API（API 失败时保留 mock 回退，不白屏）

---

## RALPLAN-DR

### Principles

- **最小代码**：新表结构与 Foreshadow 同构复用，不新建通用"卡"抽象
- **接口契约先行**：每个接口先定 Pydantic 出入 Schema（含默认值），再写路由/Service
- **无 LLM 可跑**：generate/chat 全部有确定性回退，`available=False` 时链路仍完整可演示
- **外科手术式改动**：只动 inspiration/chief 相关文件，不重构既有导演/推演链路
- **主题与类型一致**：前端类型定义以后端 Schema 为准（`types.ts` 单点同步）

### Decision drivers

1. 数据模型一致性：新表沿用 `Base + TimestampMixin` + 与 foreshadows 完全相同的表结构风格
2. 接口风格一致性：沿用 `/api/v1` + `Depends(get_service)` + async Repo，不引入新框架
3. 前端改动最小：MaestroPage 保持现有状态结构，只有状态源从 mock 换成 hook/API
4. 测试策略一致：沿用 FakeLLM（autouse fixture），新测试不加 live 依赖

### Viable options

**Option A：独立 inspiration_cards 表（chosen）**
- 实现思路：新建 ORM 模型 + 0003 迁移，Repo 增 5 个方法，Router 增 5 个路由；灵感卡有独立生命周期（可增删、采纳状态持久化、跨会话保留）
- 改动文件：`backend/app/db/models/inspiration_card.py`(新)、`alembic/versions/0003_*.py`(新)、`repo.py`、`service.py`、`routers/simulation.py`、`chief_planner.py`、`MaestroPage.tsx`、`api/novel.ts`
- Pros：状态持久化；与伏笔/章/场景平行的完整目录层；多书互不干扰
- Cons：多一张表 + 一套 CRUD（实现量最大）

**Option B：灵感卡存 JSON（books 表列）**
- 实现思路：`books` 加 `inspirations_json` Text 列，整批读写，无独立表
- Pros：实现最快，无迁移复杂度
- Cons：**无逐条 PATCH/DELETE/采纳**，并发/幂等弱；前端"采纳⇄已采纳"需整包回写，链路不完整；违背用户"编排链路完整、字段严谨"要求

**Option C：灵感卡挂在 sim 快照**
- 实现思路：跟随场景推演快照存灵感
- Cons：灵感是**书级**资产（跨场景/跨章），挂 sim 生命周期错位，被砍

**选定：Option A**。理由：用户明确要求"编排链路完整、字段严谨"，灵感池是主笔共创的核心资产，必须有独立持久化生命周期；Option A 与项目既有四层目录模式（Foreshadow 先例）完全同构，实现量在可接受范围。

---

## Implementation steps

### 阶段 0：数据地基（P0-1 前件）

1. 新建 ORM 模型 `backend/app/db/models/inspiration_card.py` — `InspirationCard(TimestampMixin, Base)`，字段：
   `id(String64,PK)` / `book_id(FK books.id,index)` / `icon(String16,"✦")` / `title(String64)` / `desc(Text,"")` / `type(String20,"plot")`（plot|character|world） / `source(String16,"chief")`（chief|author） / `adopted(Boolean,False)` / `sort_order(Integer,0)`
   参照 `backend/app/db/models/foreshadow.py:20-32`
2. 新建迁移 `backend/alembic/versions/0003_inspiration_cards.py`（`revision="0003_inspiration_cards"`, `down_revision="0002_agent_columns"`）— `op.create_table("inspiration_cards", …)`，字段同第 1 步 + created_at/updated_at，参照 `backend/alembic/versions/0002_agent_columns.py:35-49`
3. `backend/app/db/repo.py` 增 5 方法：
   - `list_inspirations(book_id) -> list[dict]`（按 sort_order）
   - `save_inspiration(data: dict) -> None`（复用 `_upsert`）
   - `delete_inspiration(card_id) -> None`
   - `get_inspiration(card_id) -> dict|None`
   - `update_inspiration_adopted(card_id, adopted: bool) -> None`
   全走 `_query_or_mem` 双模式，参照 `repo.py:394-444` 的 Foreshadow 风格
4. `backend/app/schemas/models.py`（或新 `schemas/chief.py`）增 Pydantic 模型：
   `InspirationIn({title:str, desc:str="", icon:str="✦", type:str="plot"})`、`InspirationOut(InspirationIn+{id,book_id,source,adopted,sort_order})`、`AdoptBody({adopted:bool})`

### 阶段 1：灵感池 API（P0-1）

5. `backend/app/services/service.py` 增：
   - `async def list_inspirations(book_id) -> list[dict]`（直接透传 repo）
   - `async def create_inspiration(book_id, data: dict) -> dict`（source="author"，sort_order=max+1）
   - `async def generate_inspirations(book_id, direction: str) -> list[dict]`：调 `chief_planner.generate_cards`，落库返回
   - `async def set_inspiration_adopted(card_id, adopted: bool)` / `delete_inspiration(card_id)`
6. `backend/app/services/engine/chief_planner.py` 增 `async def generate_cards(book_id, direction) -> list[dict]`：
   - LLM: `call_cheap(_CARDS_PROMPT, _CARDS_SCHEMA)`，schema = 数组 `{icon,title,desc,type}`
   - `available=False` 或解析失败 → 确定性回退 3 张模板卡（废材觉醒/宗门背叛/秘境探险）
   - 全部 source="chief"，返回规范化 dict 列表
7. `backend/app/api/routers/simulation.py` 增路由（均在 `/api/v1`）：
   - `GET /books/{book_id}/inspirations`
   - `POST /books/{book_id}/inspirations`（body=InspirationIn）
   - `POST /books/{book_id}/inspirations/generate`（body={direction:str=""}）
   - `PATCH /inspirations/{card_id}`（body=AdoptBody）
   - `DELETE /inspirations/{card_id}`（返回 204）
   参照既有路由风格 `routers/simulation.py:113-144`

### 阶段 2：主笔共创对话（P0-2）

8. `backend/app/services/service.py` 增 `async def chief_chat_stream(book_id, messages: list[dict]) -> AsyncIterator[str]`：
   - 取书骨架（`get_book_tree`）→ 拼系统上下文 prompt（title/synopsis/章节标题列表 ≤6 章）
   - `llm_client.available` → `stream_cheap_text(prompt)` 逐 token yield；否则 yield 确定性回退文案
9. `backend/app/api/routers/simulation.py` 增 `POST /books/{book_id}/chief/chat`（body={`messages:[{role,content}]`}）：
   - 返回 `StreamingResponse`（media_type="text/event-stream"），帧为 `event: token\ndata: {delta}\n\n` + 结尾 `event: done`
   - 参照既有 SSE 帧格式 `routers/simulation.py:30-33`

### 阶段 3：灵感落地 + 幂等回归（P1-1）

10. `backend/app/services/engine/chief_planner.py` 改 `plan_skelly(direction, context="", inspiration_ids=None)`：
    - `inspiration_ids` 非空时，把已采纳灵感 title/desc 作为"已在酝酿的设定"追加进 `_PLAN_PROMPT` 的 `{context}`
11. `backend/app/services/service.py` 改 `plan_book(book_id, direction, inspiration_ids=None)`：
    - 从 repo 查采纳卡 → 传 `plan_skelly`；路由层透传 body 字段（可空）
12. 回归：`commit_book_plan` 幂等逻辑（`service.py:162-208`）不触碰——新增灵感不影响其行为；跑既有 `tests/test_api.py` 确认不破

### 阶段 4：前端接真（P2 随 P0 后置，但用户全选）

13. `frontend/src/api/novel.ts` 增类型 + API：
    - `interface InspirationCard { id; book_id; icon; title; desc; type; source; adopted; sort_order }`（与后端 Schema 逐字段对齐）
    - `listInspirations(bookId)` / `createInspiration(bookId, data)` / `generateInspirations(bookId, direction)` / `setInspirationAdopted(cardId, adopted)` / `deleteInspiration(cardId)` / `chiefChat(bookId, messages)`（SSE 读取，参照既有 `useDirectorChat` 的读取模式）
14. `frontend/src/pages/MaestroPage.tsx`：
    - 灵感池状态源从 `INIT_INSPIRATIONS` 改为 `listInspirations` 加载；采纳/自建/主笔构思落真实 API
    - 对话发送走 `chiefChat` SSE（增量渲染 token），关闭失败时回退本地 reply
    - 保留「让主笔构思」真实 plan 链路不动（B10 已修）

### 阶段 5：测试（P1-2）

15. 新建 `backend/tests/test_inspiration.py`：CRUD（列表/自建/删除/采纳切换）+ generate fallback（FakeLLM available=False → 3 张模板卡）
16. 新建 `backend/tests/test_chief_chat.py`：无 LLM → 回退文案；有 fake 流 → 逐 token 断言（monkeypatch available=True + fake stream）
17. `backend/tests/test_api.py` 扩展 1 条端到端：建书→generate 灵感→采纳→plan(带 Inspiration ids)→commit（内存态）

---

## Workspace setup

- 已运行 `git status --short`（见执行记录）：工作区 **dirty**（S0-S4 + 前端 v1.0 大量未提交改动）。
- 分支 `master`，只有 1 个 init commit——**项目距上次版本快照极远，必须先提交/存档当前状态**。
- 因树已 dirty，**不创建 worktree**（dev-plan 规则：dirty 时保护现有改动，不混入新 plan）。
- 开工前**必须**：`git add -A; git commit -m "chore: S0-S4 + 前端 v1.0 存档"`（或用户自行确认现有改动），确保本次后端补齐是干净起点、可回滚。

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 0003 迁移与已建库冲突（0002 已 apply） | 迁移前先跑 `alembic current` 确认链；不在线改 schema；失败可 `op.drop_table` 回滚 |
| 灵感卡字段与前端 TS 类型漂移 | 以 Pydantic `InspirationOut` 为唯一真相，`types.ts` 对照字段清单手写同步（步骤 13 列出完整字段） |
| generate/chat 无 LLM 时链路断裂 | 全部强制确定性回退（模板卡/固定文案），AC-2/AC-4/AC-6 已锁定 |
| SSE chat 与既有 sim SSE 混淆 | 独立路由 `/chief/chat`，事件名 `token/done` 与 sim 的 `turn_*/director_*` 命名空间隔离 |
| 前端改造成本 / mock 移除引回归 | 保留 mock 回退分支（HTTP 失败时），不删除 `INIT_INSPIRATIONS`/`INIT_MESSAGES`，只降级 |
| 主笔 plan 加参数破坏既有调用 | 参数带默认值（`inspiration_ids=None`），既有 `plan_book(book_id, direction)` 签名不变 |

---

## Verification steps

- AC-1：`cd backend && .venv\Scripts\python.exe -m alembic upgrade head`（或启动时自动 init_schema 建表）后 `python -c "from app.db.models import inspiration_card; print(inspiration_card.InspirationCard.__tablename__)"` 输出 `inspiration_cards`
- AC-2：`pytest -q tests/test_inspiration.py`（新增）全绿；手动 `Invoke-RestMethod` POST generate（无 key 时返回 3 张模板卡）
- AC-3：`pytest -q tests/test_inspiration.py::test_adopt_switch` + `test_delete`
- AC-4：`pytest -q tests/test_chief_chat.py`；浏览器开 `/maestro` 输入→发送可见 token 级增量
- AC-5：`pytest -q tests/test_api.py`（含新端到端用例）
- AC-6：全量 `pytest -q` 计数 ≥ 34 passed；`cd frontend && npx tsc --noEmit` 零错误
- AC-7：前端 `/maestro`：灵感池加载真实 API、采纳切换、主笔构思、对话发送四连手动回归

---

## Pre-mortem (deliberate)

1. **Scenario**：前端对灵感卡字段期望 `typeClass`（css class），后端只给 `type`。
   **Trigger**：MaestroPage 仍用 webapp 时代 `card.typeClass` 渲染图标底色。
   **Mitigation**：后端 Schema 固定 `type ∈ plot|character|world`；前端提供映射函数 `typeClassOf(type)`，禁止透传多余字段；AC-3 已断言字段对齐。

2. **Scenario**：`generate` 调 LLM 60s+，作者以为卡死。
   **Trigger**：inspiration 生成未复用 plan 的"思考中"反馈。
   **Mitigation**：复用 `runIdeation` 现有的 `agentState` 流转（thinking→tool→idle），按钮禁用+状态灯；超时按 120s LLM 客户端超时兜底回落模板。

3. **Scenario**：`chief/chat` SSE 中间断连，前端 token 残留半句。
   **Trigger**：fetch ReadableStream 未处理 abort。
   **Mitigation**：前端 driver `AbortController`，断连时丢弃半句并提示；SSE 生成器 `finally: yield done`（参照 `_sim_stream` 的 CancelledError 处理 `routers/simulation.py:107-109`）。

---

## Expanded test plan

- **Unit**：`test_inspiration.py`（CRUD 5 用例 + generate fallback 2 用例 + sort_order/采纳语义 2 用例）；`pytest -q tests/test_inspiration.py`
- **Integration**：`test_chief_chat.py`（无 LLM 回退 1 + fake stream 逐 token 2 + done 帧断言 1）；`test_api.py` 端到端 1（建书→生成→采纳→plan→commit）
- **E2E**：浏览器 `/maestro` 四连手动回归（灵感加载/采纳/构思/对话），FakeLLM 与本机 DeepSeek 双跑
- **Observability**：`logger.info` 记 generate 卡数/chat 开启与结束；SSE 帧 `token/done` 在 `backend/logs/app.log` 可 grep；接口异常统一 `HTTPException(4xx)` + 日志警告（参照 `service.py` 既有风格）

---

## ADR

- **Decision**：灵感池采用**独立 `inspiration_cards` 表**（Option A），主笔生成与作者自建均落库，采纳状态持久化；共创对话走复用 `stream_cheap_text` 的独立 SSE 路由。
- **Drivers**：数据模型一致性（driver 1）+ 接口风格一致（driver 2）+ 用户"链路完整/字段严谨"硬要求。
- **Alternatives considered**：Option A chosen；Option B（JSON 列）rejected——无逐条生命周期、链路不完整；Option C（挂 sim）rejected——书级资产生命周期错位。
- **Why chosen**：灵感是书级可演化资产，需要独立持久化与逐条操作；与 Foreshadow 同构，实现成本可控，且为"灵感→骨架"落地留出引用锚点（book_id + id）。
- **Consequences**：
  - 正向：灵感池真实可用、采纳状态跨会话保留；plan 可引用灵感；新表可被日后"记忆包/世界观"复用。
  - 负向：多一张表 + 一套 CRUD 维护面；需 0003 迁移并在已建库执行。
- **Follow-ups**：灵感卡 → 章节/场景的直接"生成场景"拖拽落地（backlog）；灵感卡关联伏笔预埋（`foreshadows.related` 引用）；共创对话消息持久化（当前无状态流）。

---

## Review trail

- **Planner draft v1**：Option A/B/C 三选，选定独立表；10 步实施分阶段
- **Architect challenge v1**：steelman 质疑"独立表 vs JSON 列"——JSON 列看似快但采纳/幂等/并发全差;tension 为"实现量与链路完整性"——结论接受 A，但要求前端保留 mock 回退降低风险
- **Critic verdict v1**：REJECT——步骤 13 前端字段映射（typeClass）未对齐后端 type；缺 SSE 断连处理；缺 Alembic 已有库冲突的迁移预案
- **Planner draft v2**：补 Pre-mortem 3 场景、Expanded test plan、AC-3 字段对齐断言、前端 mock 回退保留、迁移 risk 行
- **Critic verdict v2**：APPROVED（改进已全部并入）；Reservation：chief/chat 消息当前无持久化，会话刷新即失，已记入 Follow-ups 不阻塞本期
- Final iterations：2 / 3