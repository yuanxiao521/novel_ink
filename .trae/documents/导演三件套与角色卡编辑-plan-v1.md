# 导演介入三件套 + 角色卡编辑 Implementation Plan

> Status: APPROVED
> Source: user request + docs/MVP设计.md §6.1-6.2 + docs/prompt核心设定.md ④角色卡层
> Mode: default（Planner → Architect → Critic，1 轮通过）
> Iterations: 1 / 3
> Author: 主笔 Agent
> Last updated: 2026-08-26

## Requirements summary

补齐作者介入的两个缺口（对好现有 `intervene` 端点与 `CharactersPage` 静态页）：

1. **导演台三件套手动注入 UI 接真**：注入事件（已有后端）· 信息曝光（后端占位）· 目标权重调整（后端占位，带因果律），把 DirectorPanel 里三个死按钮变成可用工具。
2. **角色卡编辑接真**：CharactersPage 由纯静态数据改为真实 CRUD（列表/编辑/新增/删除），落 `PUT /characters` + `spec_json`（④角色卡层字段可编辑）。

## Acceptance criteria

- **AC-1** `POST /sims/{id}/intervene` 支持 `expose`（payload: `fact_id/target_char_id/channel`）：目标角色把她能感知的 fact 记为 belief（channel 溯源），返回 success；fact 不存在 → 4xx。
- **AC-2** 同上支持 `adjust_weight`（payload: `char_id/goal_id/delta/reason`）：`reason` 为空 → 4xx（§6.1 因果律硬约束）；非空则改权重并追加一条带内因的导演 hint 事件（可感知），delta 越界被 clamp。
- **AC-3** 新增 `GET /sims/{id}/inject-palette`：返回 `{facts, characters:[{id, name, dynamic_goals:[{id, label, weight}]}]}`，供前端表单下拉。
- **AC-4** DirectorPanel 三按钮全部接真：点击出内联表单（注入事件=文本框；曝光=fact+角色+channel 下拉；调权=角色+目标+delta+必填 reason），提交失败有行内错误，成功后黑板上可见对应事件/belief 变化。
- **AC-5** CharactersPage 接真：按书+场景加载真实角色列表，basic 标签页（姓名/身份/性格/目标/口头禅/决策偏好/底线等 ④层字段）可编辑并保存（`PUT /characters/{id}`），持久化后再读回一致（内存态/DB 双路径）。
- **AC-6** pytest 新增用例（expose 成功/调权缺 reason 400/palette 返回 goals），全套跑绿；`npx tsc --noEmit` 零错误。

## RALPLAN-DR

### Principles

- 最小代码：三件套全部复用现有 `intervene` 端点与 `DirectorHint`/`InfoExposure`/`GoalAdjust` 数据模型，不加新表、不新造机制。
- 外科手术：只改 `service.intervene` 的两个占位分支 + 新增一个只读 palette 端点，不动回合自动计划链路（graph.py 已正确消费三件套）。
- 可验证：每条 AC 都是二值可断言（HTTP 状态码 / 字段存在 / UI 可见）。

### Decision drivers

1. 作者直接控制感（手动工具 > LLM 猜测）—— §6.2"人给牵引力，不是遥控器"
2. 复用现有端点/模型，改动面最小
3. 因果律不可妥协（调权必须有内因）

### Viable options

**Option A（chosen）：手动工具直连 `intervene` 端点 + 新增只读 palette**

- 实现思路：后端把 `intervene` 的 `expose`/`adjust_weight` 从 pass 补全（直接复用 graph.py 已用的 `record_belief`/`apply_goal_adjust`）；新增 `GET /sims/{id}/inject-palette` 给前端提供 facts + characters(含 dynamic_goals) 下拉数据；前端把 DirectorPanel 三个死按钮接上内联表单；CharactersPage 接真 CRUD。
- 改动文件：`backend/app/services/service.py`、`backend/app/api/routers/simulation.py`、`backend/app/schemas/models.py`（如有 channel 校验需要）、`backend/tests/test_api.py`、`frontend/src/api/novel.ts`、`frontend/src/hooks/useDirectorSim.ts`（或 DirectorPage）、`frontend/src/components/director/DirectorPanel.tsx`、`frontend/src/pages/CharactersPage.tsx`、`frontend/src/styles/app.css`
- Pros：作者逐条可控、复用已验证模型、每按钮语义单一、后端改动 < 60 行
- Cons：表单交互多（下拉×3），需要 palette 数据源；UI 工作量在 Character 页

**Option B：三件套并入"与导演共创"聊天（自然语言解析）**

- 实现思路：作者用自然语言命令导演"曝光 X 给 Y / 提高李文的家庭权重"，由 LLM 解析成三件套执行。
- Pros：交互最轻，一条输入做任意组合
- Cons：不可控、依赖 LLM 在线、误解析风险、无法满足"作者直接控制"倾向；且与共创对话语义重复（导演本就会提建议）。仅适合作为 A 落成后的增强。
- → **rejected**：本阶段作者要的是"对好接口的确定性工具"，LLM 解析留作 backlog。

**Implementation steps（基于 Option A）**

后端：

1. `backend/app/services/service.py:742-748` 补全 `intervene()` 的 `expose`/`adjust_weight` 分支：
   - `expose`：`fact = next((f for f in sim.world.facts if f.id == fact_id), None)`，无则 `InvalidActionError`；调 `WorldEngine.record_belief(char_id=target, fact_id, source_event_id="DIR", channel=channel, text=fact.text, confidence=0.8)`（复用 graph.py:55-58 同款）。
   - `adjust_weight`：`reason` 空 → `InvalidActionError("目标权重调整必须附带剧情内因（§6.1 因果律）")`；调 `world.apply_goal_adjust(GoalAdjust(...))`（world.py:160 已有 clamp 则用，无则在 service 侧 clamp delta 到 [-1,1]）；再 `_append_director_hint(sim, f"目标权重：{char_id}·{goal_id} {delta:+g}（因：{reason}）")` 让内因可感知。
2. `backend/app/services/service.py` 新增 `get_inject_palette(sim_id)`：`sim.world.facts`（运行时 facts，含 visible_to/active）+ 当前场景角色卡（`repo.get_scene_characters(sim.scene_id)`）里每个角色 `{id, name, dynamic_goals}`（`CharacterCard.spec_json` 解析，字段见 models.py:56）。facts 为空时回退 scene `initial_facts_json`。
3. `backend/app/api/routers/simulation.py` 新增 `GET /sims/{sim_id}/inject-palette`（只读，靠 `get_service` DI）。
4. `backend/tests/test_api.py` 新增 3 用例（AC-1/2/6）：`test_intervene_expose_writes_belief`、`test_intervene_adjust_weight_requires_reason`、`test_inject_palette_lists_goals`。

前端：

5. `frontend/src/api/novel.ts` 新增 `InjectPalette`/`InterveneResult` 接口 + `fetchInjectPalette(simId)`、`interveneSim(simId, action, payload)`（POST 同端点）。
6. `frontend/src/pages/DirectorPage.tsx`（或 useDirectorSim）暴露 `intervene(action, payload)`：成功后可重新拉 state 或把结果追加到 hints（最小做法：`fetchInjectPalette` 无状态，intervene 成功后提示"已生效"，下一回合黑板自然体现——避免引入轮询）。
7. `frontend/src/components/director/DirectorPanel.tsx:71-77` 三个按钮接真：点击展开内联表单（本组件内 state 管理，`div.intervene-form`）：
   - 注入事件：textarea → `intervene('inject_event', {text})`
   - 信息曝光：fact 下拉（palette.facts）+ 目标角色下拉 + channel 三选（亲见/被告知/推测）→ `intervene('expose', {...})`
   - 调整权重：角色下拉 → goal 下拉（联动）→ delta（-0.3/+0.3 快捷钮 + 输入）→ reason 文本框（必填，空则禁用提交）→ `intervene('adjust_weight', {...})`
   - 共用 loading/error/success 状态，成功后清空表单并 `console.info` 可观察。
8. `frontend/src/pages/CharactersPage.tsx` 接真：
   - 顶部加书选择器（`GET /books` + `GET /books/{id}/tree` 取第一场景）＋场景下拉；角色列表改用 `GET /scenes/{id}/characters`。
   - basic 标签页改为受控表单（姓名/身份/年龄/性格/核心目标/口头禅/决策偏好/底线），保存调 `PUT /characters/{id}`（body 组装 `spec_json` 的 CharacterCard 字段）；新增/删除接 `POST/DELETE`。
   - 无角色/无书场景给空态提示，不做假数据兜底展示。
9. `frontend/src/styles/app.css` 追加 `.intervene-form`（下拉/输入/必填标记）与角色编辑表单样式，沿用 `--accent-gold` 体系。

### Workspace setup

- 实施前跑 `git status --short` 与 `git branch --show-current`（当前：刚提交 e9a3900，树应干净，分支 master）。
- 本项目惯例一直在 master 直接开发并逐次提交（无先例 worktree）；计划默认继续 master 直改，开工前由用户确认「master 直改 or 新建 worktree」。

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| palette 的 dynamic_goals 可能为空（seed 卡未带 goals） | 前端空态文案"该角色无动态目标"，调权表单对空 goals 禁用并从 UI 隐去该角色 |
| 手调权重静默失真（§6.1） | reason 必填(后端 4xx + 前端禁用提交)，内因以 director hint 事件写回可感知 |
| delta 超界破坏权重归一化 | service 侧 clamp 到 [-1,1]（若 world.apply_goal_adjust 未 clamp 则在此补） |
| 内存态 vs DB 态 palette/保存不一致 | palette 读运行时 sim.world.facts（两态一致来源）；角色保存走 repo.save_character（内存/DB 统一） |
| CharactersPage 无"书+场景"上下文（它不在书树路由内） | 顶部加书→场景两级选择器；进入默认第一本书第一场景 |

### Verification steps

- AC-1/2：`pytest tests/test_api.py -q`（新增 3 用例 + 原有用例全绿）
- AC-3：`curl GET /api/v1/sims/<id>/inject-palette` 断言含 `dynamic_goals`
- AC-4：`npx tsc --noEmit`；手测三表单提交路径 + 空 reason 时提交按钮禁用
- AC-5：前端手测：改人设字段 → 保存 → 刷新页面读回一致
- 回归：现有 35 用例全绿（test_api 8 全绿即代表回归通过）

## ADR

- **Decision**：手动三件套走现有 `intervene` 端点补全 + 新增只读 `inject-palette`；角色卡编辑走 `PUT /characters` + `spec_json`（Option A）。
- **Drivers**：作者直接控制感（#1）决定性胜出；复用现有模型/端点（#2）控制改动面。
- **Alternatives considered**：(A) 手动直连 chosen；(B) LLM 解析自然语言 rejected——不可控、依赖在线 LLM，留 backlog。
- **Why chosen**：后端只需补两个占位分支 + 一个只读端点（<60 行），三作者可感知内因的因果律由后端强制，前端每按钮单一语义、可测。
- **Consequences**：+1 端点、+3 后端用例；DirectorPanel 交互密度上升（需内联表单样式）；CharactersPage 从静态页变为有状态页面（书选择上下文新增）。
- **Follow-ups**：三件套并入共创聊天的 LLM 版（backlog）；角色 belief/记忆编辑的深编辑（本阶段只做 basic prompt 字段）；多场景角色批量管理。

## Review trail

- Planner draft v1: 两缺口清单 + Option A/B，选 A。
- Architect challenge v1: steelman——「手动表单 vs 共创聊天二选一？palette 数据的运行态 vs 静态源？」；tension——作者控制感 vs 交互成本；综合——A 为主、B 作 backlog。
- Critic verdict v1: APPROVED。Reservations：① `apply_goal_adjust` 是否已 clamp 未在计划中确认，已补 Risk 行「clamp 到 [-1,1] 兜底」；② palette 角色数据源 `get_scene_characters` 的返回结构未 100% 对齐 `dynamic_goals` 序列化，实施第一步先验证该函数返回再写 consumer。
- Final iterations: 1 / 3（一次通过，附 2 条 reserved notes 进入实施）