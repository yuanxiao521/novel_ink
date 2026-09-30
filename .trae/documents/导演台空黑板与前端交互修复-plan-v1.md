# 导演台空黑板与前端交互修复 — 实施计划 v3

> 目标（按用户对齐结果，v3 修订）：
> ① 书架侧栏/Dashboard 接真（回书架主页入口 + 建新书）；
> ② 主笔工作台可完整选书 + 灵感卡可编辑；
> ③ 新场景「空黑板启动」——无初始事实则 facts 空（仅 stage\_desc 作环境），无角色卡则不再注入静态剧情角色；
> ④ 导演台「配置上场角色」：**混合形态** —— 空台时引导卡内联展开选角面板，已有角色后左侧角色栏用抽屉调整；`cast` 不重启局面（保留 facts/回合进度）；
> ⑤ 「全局视角」按钮接成**实时状态右滑层**（facts/信念摘要/张力/结局选项，纯前端读 sim state）。
> 死链导航（大纲/伏笔/章节/记忆包/体检/设定）**保留**，作为未来扩展点。
> 明确不进本轮：主笔/导演用 tool\_calling / skill / prompt 制定上场角色的编排（用户指定为未来扩展，另行探讨）。

***

## 0. 本轮变更记录（v3 补充 · 已完成）

> 以下为本会话实际落地的改动，是对 v2 计划范围的补充与收敛。v3 = 在完成 v1/v2 既定内容之外，新增了对齐用户最新交互诉求的改动。

### 0.1 导演台页面架构收敛为「单页下拉切换」（替代原"场景选择过渡页"）

- **背景**：v2 计划了"进导演台先弹场景树选择"的入口页；用户反馈"点了进不去导演台、体验割裂"。

- **决策**：整体改为与角色页同构的**单页结构**——不再有独立的场景选择页，也不走路由跳转整页重载。

- **落地**（[DirectorPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/DirectorPage.tsx)）：

  - 顶部栏 = **书下拉 + «章节/场景»分组下拉**（`<optgroup>` 按章节分组），同一级切换就地重载 sim。

  - 未选场景 → 工作台主体渲染**空态引导卡**（提示去主笔建结构 / 从顶部下拉选场景）。

  - 已选场景 → 场景下拉旁出现 `⟳ 新开` 按钮（丢弃进度从头推演，等价 v2 的 fresh）。

  - 用 `localStorage('director.last')` **记住最近推演场景**，重进 `/director` 自动恢复，无需重选。

  - 深链仍有效：从主笔"进入导演台"带 `book_id/chapter_id` 直达。

### 0.2 规划页完全并入主笔（删除 /planning）

- **背景**：用户反馈"plan 页面位置很尴尬"，经讨论选「完全并入主笔」。

- **落地**（[MaestroPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/MaestroPage.tsx)）：

  - 中栏「书籍骨架」支持完整 书/章/场景 CRUD：顶栏「＋」建书、工具栏「＋加章」、选中行弹出操作条（加场景/编辑/删除/进入导演台）、内联编辑表单（标题/基调/字数/小结/场景方案/舞台布置）。

  - 操作结果右上角浮出 toast 提示；带 `.selected` 高亮配色。

- **路由/骨架清理**：删除 `/planning` 路由与 `PlanningPage.tsx`；`Sidebar` active 类型去掉 `planning`；`DashboardPage` 链接改指 `/maestro`。

### 0.3 移除导演台静态兜底数据（黑板对不齐新书问题的根治）

- **背景**：用户反馈"选了新书但中间黑板仍是旧的雨夜剧情/陈默李文/第七章示例，改不了、对不上"。

- **根因**：4 个前端组件在无真实数据时回退到硬编码旧场景数据。

- **落地**：全部改为**真正的空态引导**，不再兜底静态剧情（与 v2 边界决策#1"空黑板不回退任何静态剧情"一脉相承）：

  - [CenterStage.tsx](file:///e:/novel_desk-agent/frontend/src/components/director/CenterStage.tsx)：删除硬编码环境事实（窗外下雨/保险柜）、陈默/李文角色状态、第七章示例正文 → 环境事实/事件/角色状态/成文四个区块各自空态提示 + 动态渲染真实 `state`。

  - [TimelineBar.tsx](file:///e:/novel_desk-agent/frontend/src/components/director/TimelineBar.tsx)：删除 `STATIC_TURNS` 七回合数组 → 无数据时显示"暂无回合记录"；`tl-meta` 计数去掉 `??7 / ||23` 假值。

  - [characters.ts](file:///e:/novel_desk-agent/frontend/src/components/director/characters.ts)：`charOf()` fallback 从 `CHARS[0]`（陈默）改为返回动态空 spec，避免新书角色全部错显示为陈默。

  - [CharRail.tsx](file:///e:/novel_desk-agent/frontend/src/components/director/CharRail.tsx)：按 `state.charIds` 逐个 `charOf()` 渲染（支持任意书角色），移除"裸 ID stub"分支。

  - [app.css](file:///e:/novel_desk-agent/frontend/src/styles/app.css)：新增 `.env-empty/.event-empty/.summary-empty/.timeline-empty/.prose-empty` 空态样式；`.prose-footer` 居中。

### 0.4 已通过验证

- `npx tsc --noEmit` 零错误；`npx vite build` 通过。

***

## 1. 现状分析（已核查）

### ③ 空黑板（后端装配，核心）

- `service.start()` 两处剧情兜底（`backend/app/services/service.py`）：

  - `facts = list(initial_facts) if initial_facts else list(spec["initial_facts"])` → 借 betrayal\_night 剧情事实

  - `if not characters: characters = dict(spec["characters"])` → 注入静态陈默/李文/周婶

- 依赖兜底的测试：`test_api` 的 `_make_book_chapter_scene`（无角色）、`test_start_step_state_flow`（内存 `scene-betrayal-night`）等，需适配为"建书时补书级角色卡"。

### ① 书架/Dashboard

- `Sidebar.tsx` 书架区硬编码"雨夜书房"、`新书`无 onClick、无回书架主页（/）入口；`NAV_ITEMS` 死链保留（扩展点）。

- `DashboardPage` 顶栏无建书按钮。

### ② Maestro

- 书选择仅 `books.length>1` 露出且 `slice(0,3)`（`MaestroPage.tsx` L405）；灵感卡后端无编辑端点（仅 POST/GET/PATCH adopted/DELETE/generate）。

### ④⑤ 导演台

- `CharRail.tsx` 顶部 `全局视角` 按钮纯静态；sim 角色来自装配，无法选上场。

- `useDirectorSim` 的 `SimUIState` 已含 beliefs/world.facts/tension/ending（`GET /sims/{id}/state` 即有此数据）。

***

## 2. 接口契约（新增/变更）

```
后端：
POST /books          # 已有（建书）
PUT  /inspirations/{id}      # 新增 body(部分字段): {title?, desc?, type?, icon?}
PUT  /sims/{sim_id}/cast     # 新增 body: {character_ids: string[]}（允许空=清场）
                               → 校验 ids 均为该书角色库成员（list_characters_by_book）
                               → 重建 sim.characters / action_order = ids
                               → 裁剪不再上场的 beliefs；facts/events/turn 保留（不重启局面）
                               → 返回 {sim_id, characters:[id...]}

行为变更 service.start()：
  facts: 仅 initial_facts（空则 []）；env_conds = [stage_desc]（若有）
  characters: 仅书级+特设；空则保持空（不回退 spec["characters"]）
  scratch["empty_world"] = True（当 characters 与 facts 皆空，供前端空态引导）
```

前端 `novel.ts` 新增：

```ts
updateInspiration(id, {title?, desc?, type?, icon?}) → PUT
setSimCast(simId, character_ids: string[]) → {sim_id, characters}
```

***

## 3. 实施步骤

### Step A · 后端：空黑板装配（③）

- `service.start()`：

  - `sim.world.facts = list(initial_facts)`（删 else 兜底）；`if stage_desc: env_conds=[stage_desc]`

  - 删角色静态兜底，`action_order=[]`

  - 空态标记：`if not sim.characters and not sim.world.facts: sim.scratch["empty_world"] = True`

- 核查 `graph.py` 对空 `action_order` 的容错；若空角色直接收敛，则让导演节点先产一次环境开场事件（跳过角色环节），保证空台能"开场"不崩。—— 实现时现场确认，能容错则不动。

- `run_demo.py` / `test_core` 直构 sim 不经 start，不受影响。

### Step B · 后端：灵感编辑 + cast（② 后端 / ④ 后端）

- `service.py`：

  - `update_inspiration(id, data)`：get→merge（title/desc/type/icon 白名单）→repo.save\_inspiration

  - `set_sim_cast(sim_id, character_ids)`：按 `sim.book_id` 拉书级角色建映射；校验均在册；重建 characters/action\_order；beliefs 裁剪；`repo.save` 返回

- `router`：`PUT /inspirations/{id}`（exclude\_unset）、`PUT /sims/{sim_id}/cast`（CastBody{character\_ids: list\[str]}）

### Step C · 前端：Sidebar 书架接真 + 回书架主页 + Dashboard 建书（①）

- `Sidebar.tsx`：

  - `listBooks()` 渲染真实书列表；点击书 → `Link to={/dashboard?book=id}`

  - `新书` → `showPrompt("书名")` → `createBook` → 刷新并跳 `?book=新id`

  - `NAV_ITEMS` 首位加"书架"`to="/"`（active key 'home'）；其余死链保留

- `DashboardPage`：读 `?book=` 默认选中（URL 优先）；顶栏 health 旁加"＋ 新建书"（同流程，建后切书）

### Step D · 前端：Maestro 选书 + 灵感编辑（②）

- 顶部书选择改完整 `<select>`（books 全量）

- 灵感卡操作区加"编辑"→ 内联表单（title/desc/type）→ `updateInspiration` → 刷新

### Step E · 前端：导演台选角（混合形态）+ 空态 + 全局视角（④⑤）

- **空台内联选角**（`CenterStage.tsx` + 选角面板组件 `CastPanel`）：

  - `sceneId 对应 sim` 且 `empty_world/无角色` 时，黑板中区显示引导卡"本场还没有角色 · 配置上场角色"

  - 点击 → 引导卡**就地展开**为选角面板：`listBookCharacters(bookId)` 多选（默认勾选全部）→ 保存 `setSimCast` → 引导卡消失、黑板出现角色

- **角色栏抽屉**（`CharRail.tsx`）：有角色后 rail-header 显示"配置"图标按钮 → 右侧抽屉（Drawer 浮层）同样多选 → `setSimCast` → 触发 state 刷新

- **空态渲染**：`CenterStage` 黑板无 facts 且空态标记 → "空黑板·等待开场"横幅 + stage\_desc 环境条；CharRail 无角色 → 提示卡

- **全局视角右滑层**：`CharRail` 顶部"全局视角"按钮 → 右滑层（facts 全量 + 各角色最新信念前 3 条 + 张力/趋势/回合 + 结局选项），数据全部来自现有 `SimUIState`，不做后端端点；空 sim 显示占位

- `useDirectorSim.ts`：确认/补齐 `SimUIState` 的 world/beliefs/ending 字段暴露 + `refresh()`（复用 fetch state）

### Step F · 测试与回归

- `tests/test_api.py`：`_make_book_chapter_scene` 建书后 POST 3 张书级角色卡；`test_start_step_state_flow` 改为经该 helper 建书/章/场景再 start

- 新增：

  - `test_empty_world_no_fallback`：新书空场景 start → state：characters 空、facts 空、empty\_world 标记

  - `test_cast_replaces_roles`：start 后 PUT cast 仅留 1 角色 → characters==\[x]、非上场者 beliefs 清空、turn/facts 保留

  - `test_update_inspiration`：灵感 PUT 改 title/desc 生效

- 回归全套 pytest + `npx tsc --noEmit` 零错误；Wiki 版本历史 v1.6

***

## 4. 边界与决策

1. **空黑板不回退任何静态剧情**（用户拍板）：仅 seed/真数据场景有内容。
2. **"主笔/导演制定上场角色"（tool\_calling/skill/prompt）= 未来扩展**：本轮不做（用户明确），留下空态引导作为入口。
3. **cast 不重启局面**：只换 characters/action\_order + 裁剪 beliefs，facts/events/turn 保留（用户确认）。
4. **全局视角 = 右滑层**（用户确认），纯前端读 `GET /sims/{id}/state`。
5. **选角混合形态**（用户确认）：空台内联展开 + 有角色后左侧角色栏抽屉。
6. **死链导航保留**（大纲/伏笔/章节/记忆包/体检/设定），作为后续扩展点，不加假功能。
7. **书选择链路用 URL query**（`/dashboard?book=id`），不引入全局 context。
8. 灵感"编辑"与"采纳"并存，编辑不动 adopted。

***

## 5. 验证清单

- [ ] 书架侧栏显示真实书列表；"新书"弹窗建书并跳转；侧栏可回书架主页

- [ ] Dashboard 顶栏"＋ 新建书"可建书并切换，`?book=` 生效

- [ ] Maestro 完整选书；灵感卡可编辑 title/desc/type

- [ ] 新书空场景进导演台：黑板无"雨夜书房"剧情、无静态角色；空态引导卡出现

- [ ] 空台引导卡内联展开选角 → 保存后角色上场、引导卡消失

- [ ] 有角色后角色栏抽屉可增删上场角色；cast 保留 turn/facts，beliefs 同步裁剪

- [ ] "全局视角"点击弹出右滑层（facts/信念/张力/结局），数据随 sim 刷新

- [ ] 全套 pytest 全绿（48+N）+ `tsc --noEmit` 零错误；Wiki v1.6 更新

