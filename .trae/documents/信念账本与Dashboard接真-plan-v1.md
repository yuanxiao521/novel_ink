# 信念账本 + Dashboard 概览接真 — 实施计划 v1

> 目标：①信念账本独立实体化（书级 Belief 表 + 完整 CRUD + sim 运行时同步落库）；②Dashboard 概览页尽量全接真（KPI/时间线/待办/金句/热力）。逐步有序执行，边界清晰，接口契约对齐。

---

## 1. 现状分析（探索结论）

### 1.1 信念账本现状
- 领域模型 `Belief(char_id, fact_id, source_event_id, channel, text, confidence, ts)` 已存在（`app/schemas/models.py`），`channel ∈ perceived/told/inferred`
- 信念**只存在 sim 的 `state_json.beliefs`（char_id → list[Belief]）**，`GET /sims/{id}/state` 已带出
- 写入点仅 3 处：`WorldEngine.record_belief`（engine/world.py:137）；调用方为 `graph.py:55`（角色行动沉淀）与 `service.intervene(expose)`（service.py:770）
- **无独立实体表 / 无书级查询端点 / 无作者 CRUD** —— 人物页 faith tab 是静态 `BELIEFS` 数组 + "演示样例"注释
- 回合落库锚点：`service.step()`（service.py:634 `repo.save`）、`service.stream_step()`（service.py:663 `repo.save`）、`intervene()`（service.py:790 `repo.save`）—— 信念同步就挂在这三处之后

### 1.2 Dashboard 现状
- `DashboardPage.tsx` 整页静态：`CHAPTERS/TODOS/QUOTES/HEAT` 常量 + 硬编码书名"雨夜书房"、健康分 82、KPI 7/23
- 后端可复用数据：`GET /books/{id}/tree`（章/场景/`final_prose`）、`GET /books/{id}/foreshadows`（三态+`expected_close_scene`）、sims 表（`ended` 字段，可判"导演中"）、belie ss（本次新增）

### 1.3 约定
- 迁移 0005 后 schema head；本次加迁移 **0006_beliefs**
- repo 方法 `_upsert`/delete 内存态模式复用；测试内存态（use_db=False）
- pytest 零 LLM（conftest 已全量 patch）；测试耗时已优化

---

## 2. 接口契约（Backend ↔ Frontend 对齐）

### 2.1 Belief 实体（书级账本）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 稳定主键（book+char+fact 哈希生成，幂等 upsert 用） |
| book_id | string | FK books（书级账本） |
| char_id | string | 宽松引用（不 FK characters，兼容特设/静态角色） |
| fact_id | string? | 事实溯源；手改可为空 |
| source_event_id | string | 事件溯源；手改 "AUTHOR" |
| channel | perceived/told/inferred | 亲见/被告知/推测 |
| text | string | 认知表述 |
| confidence | number 0-1 | 确信度（clamp） |
| edited | boolean | 作者手改/编辑标记（前端 badge） |
| ts | number | 产生时间（毫秒） |
| created_at / updated_at | string | 时间戳 |

### 2.2 REST 端点
```
GET    /books/{book_id}/beliefs?char_id=&channel=   → BeliefMeta[]
POST   /books/{book_id}/beliefs                      → {id}   body: BeliefBody
PUT    /beliefs/{belief_id}                           → {id}   body: 部分字段（edited 置 True）
DELETE /beliefs/{belief_id}                           → 204
GET    /books/{book_id}/dashboard                     → DashboardData
```
- BeliefBody：`{char_id: str, fact_id?: str, source_event_id?: str, channel: str, text: str, confidence?: number}`
- 校验：char_id/text 非空；channel 枚举非法→400；confidence clamp 0-1；`model_validate` 沿用

### 2.3 DashboardData（聚合契约）
```json
{
  "book": {"id","title","genre","status","chapter_count","cover_init"},
  "kpi": {
    "chapters_done": 0,          // 该章所有场景 final_prose 非空 的章数
    "chapters_total": 0,
    "word_count": 0,             // sum(len(final_prose) for scenes)
    "foreshadow_open": 0,        // buried + in_progress
    "foreshadow_closed": 0,
    "health": 0                  // 派生占位分（见 §4.4）
  },
  "timeline": [{ "chapter_id","title","order_no","tone","status","label","badge","word_count" }],
    // status: done(全场景定稿) / current(存在 active sim) / draft(有场景未定稿) / planned(无场景)
  "todos": [{ "severity":"ok|warn|danger", "title","desc" }],
    // 规则见 §4.5
  "quotes": [{ "text","author" }],     // 从 final_prose 抽「」对话；空数组兜底
  "heat": [0..27]                      // 近 4 周×7 成文量近似；全 0 空态
}
```

---

## 3. 实施步骤

### Step 1 · 后端：Belief ORM + 迁移 0006
- 新建 `backend/app/db/models/belief.py`（字段见 §2.1；无 ORM relationship，裸列查询）+ 注册 `models/__init__.py`
- 新建 `backend/alembic/versions/0006_beliefs.py`（upgrade 建表 + book_id 索引；downgrade drop；`down_revision = 0005_character_book_scope`）
- `schemas/models.py`：`Belief` 增加 `edited: bool = False`（sim 内结构向后兼容）

### Step 2 · 后端：repo 信念方法
`backend/app/db/repo.py`：
- `list_beliefs(book_id, char_id=None, channel=None) -> list[dict]`（DB 查询 + 内存态 dict 过滤）
- `get_belief(id)` / `save_belief(data)`（复用 `_upsert`）/ `delete_belief(id)`
- `list_active_sims_by_book(book_id) -> list[str]`（Simulation 表 `book_id==X and ended==False`；内存态过滤）—— 供 Dashboard "导演中"
- `_belief_dict(row)` 序列化

### Step 3 · 后端：信念同步落库 + service 方法
`backend/app/services/service.py`：
- `_sync_beliefs(sim)`：`sim.book_id` 为空直接 return；遍历 `sim.beliefs` 全部，按 `(book_id,char_id,fact_id)` 生成稳定 id，`save_belief` 幂等 upsert（confidence/channel/text/ts 覆盖，`edited=False`；fact_id 为空的手改不在此链）
- 在 `step()` / `stream_step()` / `intervene()` 三处的 `await self.repo.save(sim_id, sim)` 之后追加 `await self._sync_beliefs(sim)`
- 新增：`list_beliefs(book_id, char_id, channel)` / `create_belief(book_id, data)`（edited=True 落库）/ `update_belief(id, data)`（edited=True）/ `delete_belief(id)`
- 新增：`get_dashboard(book_id)` —— 用现有 repo（`get_book`/`get_book_tree`/`list_foreshadows`/`list_active_sims_by_book`/`list_beliefs`）聚合 §2.3

### Step 4 · 后端：router 端点
`backend/app/api/routers/simulation.py`：
- 新增 §2.2 的 6 个端点（`BeliefBody` 用 `models.BaseModel` 子类；校验同其他 body）
- 复用 `Depends(get_service)`

### Step 5 · 前端：API 封装
`frontend/src/api/novel.ts`：
- 类型 `BeliefMeta` / `BeliefBody` / `DashboardData`（对照 §2.1/§2.3）
- 函数 `listBookBeliefs / createBelief / updateBelief / deleteBelief / fetchDashboard`

### Step 6 · 前端：CharactersPage 信念账本 tab 接真
`frontend/src/pages/CharactersPage.tsx`：
- 删除静态 `BELIEFS` 与"演示样例"提示
- tab 顶部：`新增信念`按钮 → 内联表单（char_id 默认选中角色、channel 下拉、text 输入、confidence 数字）
- 列表：`GET /books/{id}/beliefs?char_id=<选中角色>` 驱动；`filter-chip` 全量/亲见/被告知/推测 ↔ `channel` 参数
- 卡片：`edited` 显示"手改"徽标（复用 belief-tag 样式家族）；编辑（点开后内联 text/confidence 修改，保存走 PUT）；删除走 `showConfirm(危险)`
- 空态文案：推演中实时沉淀；也可手动新增
- 样式沿用 `characters.css` 既有 `.belief-*` 体系，仅补 `.belief-card .hand-edited` 类

### Step 7 · 前端：DashboardPage 接真
`frontend/src/pages/DashboardPage.tsx`：
- 删除 `CHAPTERS/TODOS/QUOTES/HEAT` 常量；顶部加"书"选择（`listBooks` 默认首本）
- `useEffect(bookId)` → `fetchDashboard`；KPI 卡 / 章节时间线 / 待办 / 金句池 / 热力图全部渲染真值
- 无数据空态："暂无数据，先去规划书骨或进导演台推演"
- 标题/副标题/封面用 `book` 数据；健康分环绑定 `kpi.health`；"新章节/生成记忆包/跑体检"按钮保持占位（无对应 API）
- 样式类全部沿用（只改数据层）

### Step 8 · 测试 + 回归
- `tests/test_api.py` 新增：
  - `test_belief_crud_book_level`：POST/GET(PUT/DELETE + char_id/channel 过滤 + 非法 channel 400)
  - `test_dashboard_aggregate`：内存态建书/章/场景+finalize 定稿 + foreshadow → GET dashboard，断言 kpi/time 线状态/quotes/heat 契约字段
- 新增贯通测试（`test_core.py` 或 test_api）：`intervene expose` 后 `list_beliefs` 出现该信念（验证 `_sync_beliefs` 链路）
- 回归全套 pytest（预期 49+N passed）+ `npx tsc --noEmit` 零错误

### Step 9 · 文档
- `docs/代码导航与Bug追踪Wiki.md` 版本历史加 v1.5 行 + 变更日志

---

## 4. 边界与决策（Assumptions & Decisions）

1. **信念 = 书级账本**：落库后跨 sim 跨场景持续累积；**自动同步只增不删**（sim 回退不回滚表），作者用 DELETE 清理（账本语义，避免回退连带）
2. **手改标记**：所有 POST/PUT 写库 `edited=True`；sim 自动产出的渠道标记 `edited=False`。前端"手改"徽标依据 `edited`
3. **char_id 宽松引用**：不建 characters FK（兼容场景特设/静态模板角色 id），由业务层校验角色存在于该书角色库时警告而非 400
4. **健康分 = 派生占位**：`100 - open×3 - undone_chapters×5 - overdue×10`（clamp 0-100），UI 注明"预估健康"，非真实体检 API
5. **金句/热力为近似**：quotes 从 `final_prose` 抽含「」/引号对话行（≤3，空数组兜底）；heat 按各场景定稿日期将字数散布 28 格（无定稿全 0 显示空态）—— 数据语义不足处用近似而非造假
6. **伏笔"逾期"简化**：数据里无"当前进度"概念，故不做严格逾期算法；todo 用"待回收伏笔 N 条（open 且已设期望回收场景）"代替逾期告警（原静态 TODOS 语义对齐）
7. **不新增 sims FK 迁移**：Dashboard 用现表 `ended` 字段查询，无 schema 变更
8. **角色页 memory/relation 两个 tab 不在本次范围**（仍静态演示，保持提示），后续独立迭代

---

## 5. 验证清单

- [ ] 迁移 0006 在真库执行成功（alembic upgrade head）
- [ ] `GET /books/{id}/beliefs` 空数组；POST 后返回 id；PUT 改 confidence 后 GET 反映且 `edited=true`；DELETE 后 204
- [ ] simulate → `intervene expose` → `list_beliefs` 出现该信念（自动同步链路通）
- [ ] `GET /books/{id}/dashboard` 返回契约全字段；定稿场景后 kpi.word_count>0、章节状态 done
- [ ] 人物页信念 tab：筛选/新增/编辑/删除可用，无静态示例残留
- [ ] Dashboard：切书后 KPI/时间线/待办/金句/热力全部真数据（无数据时空态）
- [ ] 全套 pytest 全绿（零 LLM 调用）；tsc --noEmit 零错误
- [ ] Wiki v1.5 更新