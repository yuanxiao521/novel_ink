# 阶段④ 正文协作工作区（prose-studio）Implementation Plan

> Status: APPROVED
> Source: `docs/主笔Agent升级设计.md` v0.2 §4.6（用户确认的方案）
> Mode: default（完整 Planner → Architect → Critic loop）
> Iterations: 1 / 3
> Author: 主笔 Agent × 作者（方案经 4 轮 AskUserQuestion 收敛）
> Last updated: 2026-09-02

## Requirements summary

为场景正文建立多角色协作工作区：✍ 写手（生成初稿）、🩺 体检员（问题清单报告）、🎨 润色师（去 AI 味、只改写法不动剧情）、🔍 质检员（伏笔/信念/因果对照 → 作者确认后才记账）。**手动为主 + 可选自动**；每次体检/润色/质检写一条 `prose_notes` 审计记录（可追溯），作者 approve/reject 后才生效；正文落 `scene.final_prose`（幂等）。

## Acceptance criteria

- AC-1 场景详情可「写手生成初稿」并进入正文编辑区；无 LLM 时确定性回退（不白屏、不落脏稿）

- AC-2 「体检员体检」产出结构化报告（issues 数组），写入 editor note（pending）

- AC-3 「润色师润色」返回润色稿 + 改动摘要，写入 polisher note（pending），\[应用] 才替换正文

- AC-4 「质检员质检」对照伏笔/信念账本产出意见（foreshadow\_updates / belief\_deltas / causal），存入 verifier note（pending）

- AC-5 作者 approve verifier note → 触发记账写库（伏笔推进/信念/因果），同一事务内先记账后置 approved；reject → 仅置 rejected 不写库

- AC-6 `GET /scenes/{id}/prose/notes` 返回全量审计记录（含状态），前端列表可追溯

- AC-7 作者「保存正文」把 final\_prose 落库（幂等：同内容 hash 重复保存不重复记账）

- AC-8 后端测试新增 test\_prose\_studio.py 全绿，全量 pytest 无回归；前端 tsc 零错误

## RALPLAN-DR

### Principles

- 最小代码：四角色能力共用一份"读输入→LLM→校验→写 note"样板，不引入注册表/工作流框架

- 外科手术：只动 scenes 正文链路；不碰导演台/回合循环/骨架

- 可验证：每步产出有校验器（复用 `validate_and_retry`），无 LLM 走确定性回退

- 审计可追溯：一切修改（体检/润色/质检）都落 prose\_notes，作者审阅是唯一生效闸门

### Decision drivers

1. **作者在环**（用户拍板：重要修改需审阅确认）→ 记账必须延迟到 approve
2. **角色职责分离 + 可并行**（体检/质检互不依赖）→ 独立接口，前端并行发
3. **手动为主 + 可选自动** → 能力是"可按钮调用"，不强制成链
4. **复用现有资产**（阶段③校验器、阶段①记忆、skeleton-to-prose 反 AI 味规则）

### Viable options

**Option A: engine/prose.py 四能力函数 + 薄 Service 编排 + 前端抽屉面板**（favored）

- 实现思路:新模块 `app/services/engine/prose.py` 提供 `draft/review/polish/verify` 四个 async 函数（内部共享 `_run_role` 样板:拼上下文→LLM(流式/结构化)→`validate_and_retry`→确定性回退）;Service 层负责读写场景/账本/notes;前端 MaestroPage 场景选中弹「正文协作」抽屉

- 改动文件:`backend/app/services/engine/prose.py`(新)、`service.py`、`routers/simulation.py`、`frontend/src/components/backoffice/SceneStudioPanel.tsx`(新)、`MaestroPage.tsx`、`novel.ts`

- Pros:与现有 engine/ 风格一致;后端自包含可测;前端最小侵入(一个面板)

- Cons:四 role 拆四个函数,prompt 有相似样板(已用 `_run_role` 收敛);检测并行能力,但**本轮不做自动并行编排**

**Option B: 用 langgraph 把四角色编排成一条工作流图**

- 思路:仿 director graph,建 `prose_graph`(draft→review→polish→verify 节点),支持自动批量 + 回放

- Pros:天然支持并行/串行编排、可回放

- Cons:**过度设计**——当前是"手动按按钮 + 作者确认"场景,无自动多步需求;langgraph 事件流复杂度远高于收益

- Invalidation:手动为主 (driver 3) 让"自动编排图"本轮无价值;阶段⑥ tool-calling 再评估

**Option C: 直接往 chief\_planner.py 塞 prose 方法**

- 思路:在现有主笔模块里加 prose 生成/体检函数,文件最少

- Pros:改动文件数最少

- Cons:职责混淆——正文协作 ≠ 主笔规划;四角色 + 记账逻辑会让 chief\_planner 肿胀,违反 §4.6"多角色分工";运行期重构成本高

- Invalidation:违反 principle「职责分离」与项目四层分工约定

### Implementation steps

1. 执行迁移 0009（建 `prose_notes` 表）— `backend/alembic/versions/0009_prose_notes.py`（已创建，待 `alembic upgrade head`）
2. 模型 + repo — `backend/app/db/models/note.py` 新增 `ProseNote`（scene\_id/kind/status/suggestion/before/after/created\_by/reviewed\_at/ts）；`models/__init__.py` 注册；`backend/app/db/repo.py` 加 `list_prose_notes/save_prose_note/set_prose_note_reviewed`
3. 四角色能力 — `backend/app/services/engine/prose.py` 新建：

   - `draft_prose(llm, scene, memory_ctx)` → 正文初稿（`chat_stream` 流式；无 LLM→确定性提示文案）

   - `review_prose(llm, text, scene_ctx)` → `{issues:[{severity,text,suggestion}], overall}` + validator + 确定性回退

   - `polish_prose(llm, text, style)` → `{after, summary}` + 软校验（与原文是否纯措辞差异）+ 回退

   - `verify_prose(llm, text, foreshadows, beliefs)` → `{foreshadow_updates:[], belief_deltas:[], causal:[], risks:[]}` + validator + 回退

   - 共享 `_run_role(...)` 样板：拼 prompt→call→validate\_and\_retry→fallback
4. Service 编排 — `backend/app/services/service.py`：

   - `prose_draft/review/polish/verify(scene_id)`：读场景+书级记忆(chief\_perceive)+账本 → 调 engine → 写 prose\_note(pending)

   - `list_prose_notes(scene_id)` / `review_prose_note(note_id, action)`：**approve 对 verifier note 先记账（伏笔 save\_foreshadow 推进 / beliefs save\_belief / 因果写 notes.suggestion）再置 approved；失败则保持 pending 并抛错**；reject 仅置 rejected

   - `save_scene_prose(scene_id, text)`：final\_prose 幂等落库（hash 判重，重复保存不同步记账）
5. API 路由 — `backend/app/api/routers/simulation.py`：

   - `POST /scenes/{id}/prose/draft`（流式 SSE：token/done，复用 `_sse`）

   - `POST /scenes/{id}/prose/review` / `polish` / `verify`（JSON：note + 产出）

   - `GET /scenes/{id}/prose/notes`；`POST /prose-notes/{id}/approve|reject`

   - `PUT /scenes/{id}/prose`（作者保存正文）
6. 前端 — `frontend/src/api/novel.ts` 加 ProseNote/ProseStudio API；`components/backoffice/SceneStudioPanel.tsx` 新建（正文 textarea + 写手/体检/润色/质检按钮 + 报告列表 + 审计记录列表 + approve/reject + 保存正文）；`MaestroPage.tsx` 场景选中时「✎ 正文协作」入口（复用 Dialog 体系 + maestro.css 变量）
7. 测试 — `backend/tests/test_prose_studio.py`：notes 生命周期（pending→approve 记账/reject 不记账）、无 LLM 回退、幂等保存、审阅顺序（approve 前先记账）

### Workspace setup

- 已执行检查：`git status --short` ⇒ 工作区 dirty（阶段①②③ + 文档改动未提交，迁移 0005-0009/模型 memory/Belief 等为未跟踪文件），分支 `master`

- 遵循项目惯例 **直接在 master 推进、里程碑打 tag**，本次**不建 worktree**（历史 v1.6-v1.8 均如此）

- **建议**：实施前先把阶段①②③ 的现有改动 commit 一次（需用户确认再执行），避免与本阶段 diff 混杂

- open question → 见下

### Open questions

- 是否先提交阶段①②③ 的存量改动再开工？（建议是，用户决定）

- 体检/润色产出是否需要"应用"到正文的二次确认交互（diff 预览）——按 AC-3 已有 \[应用] 设计

## Risks & mitigations

| Risk                               | Mitigation                                                       |
| ---------------------------------- | ---------------------------------------------------------------- |
| 无 LLM 时四角色不可用 → 前端卡死               | 每角色确定性回退（draft 提示文案 / review 空报告+提示 / polish 原文+摘要 / verify 空意见） |
| approve 记账与 note 状态不一致（账本写成功但状态没置） | 同一事务/顺序：**先写账本，成功后再置 approved**；异常抛错保持 pending（AC-5）             |
| 质检意见落 suggestion JSON 体积大          | suggestion 只存意见摘要（每条 ≤200 字），结构化明细不落库（避免胀库）                      |
| 前端面板复杂度过高                          | 首版最小：textarea + 按钮 + 列表，不做 diff 高亮/拖拽                            |

## Verification steps

- AC-1/2/3/4：`pytest tests/test_prose_studio.py -q` 全绿 + 手动调四接口（无 LLM 时观察回退）

- AC-5：单测断言 approve→foreshadows/beliefs 行数变化、6 字段生效；reject→账本不变

- AC-6：`GET /scenes/{id}/prose/notes` 返回 pending/approved/rejected 混合记录

- AC-7：重复保存同正文 → 只一条 env 变更、ff 幂等

- AC-8：`pytest tests -q` 无回归（预期 65 passed 附近）；`cd frontend && npx tsc --noEmit` 零错误

- 迁移：`alembic upgrade head` 成功、`\d prose_notes` 结构正确

## ADR

- **Decision**：四角色（writer/editor/polisher/verifier）作为 `engine/prose.py` 独立能力函数 + 薄 Service 编排 + `prose_notes` 审计表 + 前端抽屉面板；`verify` 记账前置于作者 approve

- **Drivers**：作者在环（审阅确认）决定性促成"记账=approve 后置"；角色分离+可并行促成独立接口；手动为主促成轻编排

- **Alternatives considered**：A 四函数能力（chosen）；B langgraph 工作流图（rejected——手动为主无自动编排需求，过度设计）；C 并入 chief\_planner（rejected——职责混淆）

- **Why chosen**：最小代码 + 后端自包含可测 + 前端单面板接入；审计表让一切修改可回查

- **Consequences**：+1 表（prose\_notes）、+1 引擎模块、+1 前端面板；为阶段⑤记账提供"确认后写入"入口；体检/质检可并行但不自动编排（留给阶段⑦）

- **Follow-ups**：阶段⑤ 完整记账 Agent（含保存正文自动触发）；润色独立 SKILL.md（skill-creator）；可选自动体检/润色开关

## Review trail

- **Planner draft v1**：Option A/B/C + 最小实现步骤 + 弱校验集中在"无 LLM 回退"与"approve 记账一致性"

- **Architect challenge v1**：steelman——四函数重复样板（每个都是"读输入→LLM→校验→写 note"），可合成 `_run_role` 分发器；tension「能力合一（简单）vs 角色独立（可扩展）」→ 采纳 `_run_role` 收敛样板但保留四角色入口函数，不引入注册表

- **Critic verdict v1: APPROVED**（7 维度全过）

- **Critic reservations**：① approve 记账的失败一致性——若账本写失败而 note 已置 approved 会留下"已批准但未记账"的脏状态 → 已落实为先记账后置状态（AC-5）；② 质检明细体量——suggestion 全文入库可能胀库 → 限定每条意见 ≤200 字存摘要

- Final iterations: 1 / 3

