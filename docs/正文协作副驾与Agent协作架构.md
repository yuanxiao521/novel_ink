
# 正文协作副驾 与 Agent 协作架构（设计稿）

> 版本 v0.1 · 2026-10-01 · **已确认（待落地）**
> 定位：在已交付的 **S1/S2/S3/S4 + A2/A3 + T9** 之上，补上「**编排层**」与「**场景层副驾**」两块。
> **不动引擎**：`services/engine/*` 的写手/体检/润色/质检/质量回环/AI 味/定点修复、0-token 校验层、三本账幂等 upsert 全部复用。
> 配图（8 张，本目录）：[流程总图](mockups/design/flow-overview.png) · [副驾架构](mockups/design/agent-architecture.png) · [现状 agent 清单](mockups/design/agent-inventory.png) · [五模块×通信三层](mockups/design/agent-modules.png) · [最终架构](mockups/design/final-architecture.png) · [感知包](mockups/design/percept-packet.png) · [复用选择](mockups/design/studio-agent-choice.png) · [主笔↔责编信息流](mockups/design/chief-editor-loop.png)

---

## 0. 已确认决策（本次拍板）

| # | 决策 | 结论 |
| --- | --- | --- |
| 1 | 架构骨架 | **五模块（感知/记忆/上下文/执行/边界）× 通信三层（黑板/账本/任务总线）× 记忆四层** |
| 2 | 正文协作 agent | **新建「责编」（场景级）**，与主笔**共享底座**（约束/工具/账本读取/审计/闸门），**不共享人格** |
| 3 | 感知包 | `perceive(scope) → PerceptPacket`，**先只落主笔 + 责编两个样板**，行为等价 |
| 4 | 写权限矩阵 | **本批只出草案**（A+ 启用） |
| 5 | 角色 agent | **不允许发起任务**（本就如此）；本次只补 AgentSpec 声明 + 走统一感知出口 |
| 6 | 消息落库 | **直接落 PG**（`agent_tasks`/`agent_messages`），**当前不引 Redis** |
| 7 | 主笔↔责编 | **下行约束 + 上行信号 + 按需复盘**；互相**只能建议 + 确认**，不互相直接改 |
| 8 | 导演台定位 | **可选彩排 · 场景级**：主笔建议 / 责编请求 / 作者开关，三方都能发起 |

---

## 1. 现状盘点（照代码核对）

### 1.1 角色化 agent 7 个 + 0-token 规则层 + 空着的编排位

| 层 | 角色 | 读 | 写 | 触发 | LLM |
| --- | --- | --- | --- | --- | --- |
| 结构 | **主笔** `chief_planner` | 书树/书级记忆/灵感/伏笔/世界状态 | 灵感卡·骨架·角色卡·世界观·对话 | 作者 | strong |
| 演绎 | **导演 Agent** `director` | 黑板/角色状态/回合史/张力 | 每回合软引导 | 回合循环 | strong |
| 演绎 | **角色 Agent ×N** `character` | 自身卡/感知事实/信念账本 | thought·action·emotion | 回合循环 | strong |
| 文字 | **写手** `prose.draft_prose` | 场景 4 字段/角色卡/世界状态/涌现素材 | 正文初稿 | 作者 | strong |
| 文字 | **体检员** | 正文/角色卡/口吻先验 | issues·voice_findings | 作者 | strong |
| 文字 | **润色师** | 正文/问题清单 | 改写稿 | 作者 | strong |
| 文字 | **质检员** | 正文/伏笔/账本 | 推进·信念·因果·风险·state_deltas | 作者 | strong |
| 文字 | **评审**（无身份） | 正文 + 硬信号 | 分数·弱项 | A2 回环内部 | strong |
| 账簿 | **记账** | 质检 deltas | 伏笔三态/信念/世界状态 | 批准后自动 | **无** |
| 校验 | **规则器组** | 文本/账本/结构 | 命中·拦截·诊断·派生 | 各处 | **0-token** |
| 编排 | **（缺位）** | — | — | — | — |

### 1.2 五模块现状

| 模块 | 现状 | 判定 |
| --- | --- | --- |
| 感知 | `character.perceive_context` 是**唯一做对的样板**（视角隔离 + `perceivable_facts` 可见度裁剪 + 近况 6 条）；其余 agent 全量注入 | **仅角色有** |
| 记忆 | 四堆混用：`book_memories`（主笔独享）/ 三本账 / `TurnArchive`（**正文协作不读**）/ `prose_notes`（**agent 不读**） | **有账本 · 无分层** |
| 上下文 | 四层来源只在角色 agent 落地（`_compose_prompt`）；其余各自硬编码拼串 | **无统一装配器** |
| 执行 | 结构化 JSON + `schema_retry` + service 落库 | **有雏形 · 无工具层** |
| 边界 | 口头约定 + 护栏 + 事实闸门兜底 | **无显式声明** |

### 1.3 通信现状（两层）

- **L1 黑板**：`SimulationState` 即黑板对象（LangGraph State 持 `sim`，节点内原地改）→ 节点顺序执行 = 隐式同步。
- **L2 账本**：**通过数据库中介的间接通信**（落库 → 再装配 prompt；stigmergy）。优点：可审计/可回放/崩溃可恢复；缺点：不能"问一句"、不能协商、能力不可发现。
- **L3 消息层**：**不存在** —— 这是"按钮 vs 副驾"和"请求彩排"的根因。

---

## 2. 目标架构

分五层（见[最终架构图](mockups/design/final-architecture.png)）：

```
L0 作者（唯一人类·总导演）      对话 / 按钮 / 选中批注 / 确认闸门
L1 编排与执行层（全新）          SupervisorAgent · ToolRegistry · ToolExecutor · TaskBus
L2 Agent 层                     每个 agent 带五模块；AgentSpec 声明能力
L3 通信三层                     L1 黑板（内存） · L2 账本（PG） · L3 任务总线（PG）
L4 校验层（0-token·横切·不重写）  style_checks · world_rules/guard · fact_gate · schema_retry · global_view · emergence · degradation
L5 存储                         PostgreSQL（唯一事实来源）；Redis 当前不引
```

### 2.1 AgentSpec（能力声明 · 纯数据）

```python
@dataclass(frozen=True)
class AgentSpec:
    id: str                 # "chief" | "editor" | "stage_manager" | "character" | "writer" | ...
    name: str               # 展示名：主笔 / 责编 / 场记
    scope: str              # book | scene | round | task
    persona: str            # system prompt 模板名
    reads: list[str]        # 感知来源引用：["book_tree","memories","ledgers","turn_archives","annotations"]
    writes: list[str]       # 落点：["outline","characters","final_prose","annotations"]
    tools: list[str]        # 可用工具名（白名单）
    model_tier: str         # strong | cheap | none
    emits: list[str]        # 产出事件类型（SSE/审计）
    can_initiate_tasks: bool  # 角色 agent = False
    needs_confirm: list[str]  # 需作者确认的工具名
```

> 落地：`backend/app/services/agents/spec.py`。**只声明不执行**；由 ToolExecutor 校验"这个 agent 能不能调这个工具"。

### 2.2 五模块定义

| 模块 | 统一接口 | 说明 |
| --- | --- | --- |
| 感知 | `perceive(scope, ctx) -> PerceptPacket` | 四种 scope：`character_view` / `scene` / `book` / `task` |
| 记忆 | 四层（见 §4） | 短期 / 工作记忆 / 长期事实 / 约束与偏好 |
| 上下文 | `render(spec, packet) -> prompt` | 同一包喂不同 agent，只换模板；保留现有四层顺序 |
| 执行 | `ToolExecutor.call(tool, args, who)` | 校验 → 闸门 → 执行 → 审计 → 事件流 |
| 边界 | `AgentSpec` + 写权限矩阵 | 越权直接拒绝（有测试） |

---

## 3. Agent 职责与边界（定稿）

| agent | 层 | scope | 掌管 | **不能做** |
| --- | --- | --- | --- | --- |
| **主笔** | 结构 | book | 书树/节奏/伏笔/角色弧；**维护约束表**；读写作侧信号后给建议 | **不能改正文**；不能自动改骨架（要作者确认） |
| **责编** | 场景 | scene | 本场正文/口吻/事实一致/伏笔落地；读批注；可**请求彩排** | **不能改骨架与约束** |
| **场记**（原"导演 Agent"） | 演绎 | round | 每回合软引导（只给方向不给台词） | 不能改正文/骨架；不能发起任务 |
| **角色 Agent ×N** | 演绎 | round | thought/action/emotion；经黑板表达 | **不能发起任务**；不能看全量黑板（视角隔离） |
| **写手/体检/润色/质检** | 文字 | scene | 各自的产物 | 由 ToolExecutor 编排，不各自直连路由 |
| **评审** | 文字 | scene | 分数/弱项（A2 内已有） | D 批给身份与入口 |
| **记账** | 账簿 | book | 三本账幂等 upsert | 只由"批准"触发 |
| **编排者** | — | task | 意图→计划→选工具→汇报 | **不创作、不判断好坏** |

---

## 4. 记忆四层

| 层 | 生命周期 | 谁写 | 谁读 | 现状 |
| --- | --- | --- | --- | --- |
| 短期 | 回合 / 本轮对话 | 各 agent 自己 | 仅自己 | 只有主笔有 `chat_histories` |
| **工作记忆** | 场景 | ToolExecutor / 作者批注 | 责编 · 写手 | **缺**（A 批补：批注落库） |
| 长期事实 | 书 | 记账（唯一） | 全员读**裁剪视图** | 已有三本账 |
| 约束与偏好 | 书 | 主笔 / 作者 | **全员读** | 部分（B 批：约束表全局化） |

---

## 5. 通信三层与写权限矩阵（草案）

### 5.1 三层职责

| 层 | 传什么 | **不传什么** |
| --- | --- | --- |
| L1 黑板（内存） | 回合内事实/事件/scratch | 不跨回合（回合边界归档） |
| L2 账本（PG） | 长期事实与状态 | 不传请求与协商 |
| L3 任务总线（PG） | **请求 / 裁决 / 通知** | **不传状态**（防第二套事实来源） |

### 5.2 写权限矩阵（草案 · A+ 启用）

| 目标 | 唯一写者 | 读者 |
| --- | --- | --- |
| `chapters` / `scenes` 结构（增删改字段） | 主笔（作者经 UI） | 全员 |
| `scenes.final_prose` | 责编 / 作者 | 全员 |
| `characters` | 主笔 / 作者 | 全员 |
| `book_memories` | 主笔 / 作者 | 全员 |
| `beliefs` / `world_states` / `foreshadows` | **记账（唯一）** | 全员读裁剪视图 |
| `prose_notes` | **ToolExecutor（唯一）** | 作者 · 汇总器 |
| `prose_annotations` | 作者创建；工具改状态 | 责编 · 写手 |
| `agent_tasks` / `agent_messages` | 三方发起者写；编排者改状态 | 全员 |
| `sim.world.facts`（黑板） | `refresh_world`（回合内唯一） | 角色读**裁剪子集** |

---

## 6. PerceptPacket 规范（先落主笔 + 责编）

见[感知包对比图](mockups/design/percept-packet.png)。结构：

```json
{
  "who": {"id": "...", "name": "..."},
  "scope": "character_view | scene | book | task",
  "sources": ["card", "beliefs", "facts", "events"],
  "budget": {"max_tokens": 900, "used": 412},
  "blocks": [
    {"kind": "self",    "text": "..."},
    {"kind": "goals",   "items": [{"text": "...", "weight": 0.7, "reason": ""}]},
    {"kind": "beliefs", "visibility": "own",      "items": [{"text": "...", "channel": "亲见", "src_event": "E-003"}]},
    {"kind": "facts",   "visibility": "filtered", "items": [...], "dropped": [{"id": "F-2", "reason": "visible_to 不含本角色"}]},
    {"kind": "events",  "window": "last6",        "items": [...]}
  ]
}
```

**四条收益**：① 可断言（"写手的包里不含 F-2"能写测试）；② 裁剪留痕（`dropped` 带原因 → 落实"静默降级必须反馈"）；③ 一处产出多处渲染；④ 可存档复现。
**行为等价**：现有四层 prompt 顺序不变，只是中间多一层可检查的中间产物。

---

## 7. 工具清单（A 批：**只收本场文字类**）

| 工具 | 副作用 | 需确认 | 复用 |
| --- | --- | --- | --- |
| `prose.scan_tone` | read | — | `style_checks.ai_tone_report`（0-token） |
| `prose.quality_score` | read | — | `quality.score_text` |
| `scene.get` / `character.list` / `foreshadow.list` / `world_state.list` / `annotation.list` | read | — | repo |
| `prose.draft` | write（候选） | — | `prose.draft_prose` |
| `prose.review` | write（候选） | — | `prose.review_prose` |
| `prose.polish` | write（候选） | — | `prose.polish_prose` |
| `prose.spot_fix` | write（候选） | — | `ai_tone.spot_fix`（含事实闸门） |
| `prose.quality_loop` | write（候选） | — | `quality.quality_loop` |
| `prose.verify` | write（候选） | — | `prose.verify_prose` |
| `prose.save` | **destructive** | ✅ | `saveSceneProse` |
| `verify.approve`（落账） | **destructive** | ✅ | `_apply_verify_bookkeeping` |
| `annotation.resolve` / `annotation.delete` | **destructive** | ✅ | 新表 |

**第二批（跨页，需先立边界）**：`scene.patch` · `memory.write` · `director.step` · `task.create`（请求彩排）。

**编排方式（关键取舍）**：现有客户端只有 `call_strong(prompt, json_schema)` + `response_format=json_object`，**无 function calling** → 第一版走 **「计划式工具调用」**：模型只输出 `{reply, actions:[{tool,args,why}]}`（复用 `schema_retry` 校验纠错），**由本地 ToolExecutor 执行**。可测、可审计、FakeLLM 全绿；真 function calling 等需要多轮探索再上。

---

## 8. 新增数据（三张表 · 不改现有表）

### 8.1 `prose_annotations`（批注 · 迁移 0014）
`id` / `scene_id` / `para_index` / `quote`（引用原文，用于校验定位）/ `note`（作者意见）/ `status`(`open|handled|dismissed`) / `created_by` / `handled_by_note_id` / `created_at` / `updated_at`

> **为什么落库**：不是为了"给 agent 看"（塞进当轮上下文即可），而是把一次性口述变成**可追踪的改稿任务**——刷新不丢、状态可查、可批量、可归因（对应 `prose_notes` 的"谁发起"）、能做**批注热点**反哺约束。

### 8.2 `agent_tasks`（任务单）
`id` / `book_id` / `scene_id` / `from_agent` / `to_agent` / `kind`(`rehearsal|expose|adjudicate|rewrite`) / `goal` / `input_ref`(JSON) / `status`(`submitted|working|input-required|completed|failed`) / `artifact_ref` / `created_at` / `updated_at`

> `input-required` **复用现有"确认闸门"**；`artifact_ref` 指向已有产物（骨架 / TurnArchive / final_prose / prose_notes）。

### 8.3 `agent_messages`（消息）
`id` / `task_id` / `role`(`request|response|notify`) / `from_agent` / `to_agent` / `content_json` / `created_at`

---

## 9. 主笔 ↔ 责编 信息流（闭环）

见[信息流图](mockups/design/chief-editor-loop.png)。

- **① 下行 · 约束**（已有）：约束表 / 书树 / 伏笔计划 / 建议彩排 —— **只读约束 + 场景任务**。
- **② 上行 · 场景信号包**（缺 → 本次补）：字数达成 / 本场张力 / 伏笔埋收 / 口吻偏离 / **批注热点** / 重写次数与被驳回项 / 偏离场景目标。
  - **只回"发生了什么"（结构化），不回全文**（防上下文爆炸与越界）。
  - **不改表**：数据源已有（`prose_notes` 的 kind/status/created_by + `TurnArchive.tension` + 批注状态 + `final_prose` 字数），在 S3 `global_view.py` 上增一段 **0-token 派生的「写作侧信号」**。
  - **三个落点**：正文保存后（已有 `_bookkeep_after_save` 钩子）· 场景收束 · 概览复盘。
- **③ 横向 · 请求-应答**（缺）：责编 → `agent_tasks` → 编排者派给场记/角色 → 素材回填本场（勾选才注入）。
- **两条硬边界**：**主笔不改正文** · **责编不改骨架与约束**；互相**只能建议 + 确认**。
- **主笔读信号的时机**：作者召唤时读**汇总**；概览点「让主笔复盘」读全书信号 → 产出**建议**；**场景收束不自动叫主笔**（不打断心流）。

---

## 10. Redis 判定：当前不引

**纪律**：Redis 只做**传输 / 缓存 / 锁**，**绝不放业务事实**（三本账、正文、任务单真值永远在 PG）。

触发条件（任一成立再引）：① 多进程（`--workers>1` 或独立 worker，黑板与 SSE 需跨进程）；② 后台长任务（批量写手/批量彩排 → 队列与断点）；③ 多人实时（pub/sub 广播）；④ 高频进度（进度/心跳写 PG 太重）。

**当前判定**：A/A+ 批任务单量小、要审计、要跨会话 → **直接落 PG，不引 Redis**。

---

## 11. 批次与验收

| 批 | 内容 | 验收（可执行） |
| --- | --- | --- |
| **A** | ToolRegistry + ToolExecutor + **责编**（对话 + 批注）+ `prose_annotations` + 7 个按钮**全部工具化** —— **后端已落地 v1.18**（前端待做） | ① 按钮与 `POST /scenes/{id}/agent/chat` 走**同一工具**（审计里都能看到，带"谁发起"）；② 批注 CRUD + 状态机；③ FakeLLM 下计划式工具调用可测；④ `tsc` 0 + `pytest` 全绿 |
| **A+** | `AgentSpec` + `agent_tasks`/`agent_messages` + 写权限矩阵**启用** | ① 越权调用被拒（含测试用例）；② "请求彩排"落一条 task 并可见；③ 状态机含 `input-required` |
| **B** | 约束表全局化 + `perceive(scope)`（先主笔+责编） | ① 同一 PerceptPacket 渲染两种 prompt；② `dropped` 留痕有测试；③ 行为等价（现有用例不回归） |
| **C** | 彩排三方决策（主笔建议 / 责编请求 / 作者开关）+ **素材使用率** | ① 第一条真实任务单闭环；② 场景行显示"采纳 N 条 · 引用 M 条" |
| **D** | 文字层合并为**一次审稿会** + 评审给身份 | ① 按钮从 7 个收敛；② 审计里出现"评审" |
| **E** | 角色在场感（正文页"角色意见"只读卡） | 复用 `character.think`，不改角色 agent 边界 |

**顺序**：A → A+ → B → C → D → E。**样板先行**：五模块只先落 **主笔** 与 **责编**，其余 agent 保持现状再推广。

---

## 12. 明确不做

1. **多 agent 自由对话自治**（成本不可控、不可审计）。
2. **动 0-token 校验层**（全项目最可靠的部分，只该被工具化）。
3. **让编排者拥有创作判断**（它只调度与汇报）。
4. **当前引入 Redis**（见 §10）。
5. **主笔读正文全文 / 责编改骨架**（互相越界会互相覆盖）。
6. **一次改 7 个 agent**（先两个样板，跑通再推广）。

---

*本文档为 v0.1 设计稿；落地后按 §11 批次推进，并把「五模块/通信三层/AgentSpec」并入 [代码导航与Bug追踪Wiki.md](代码导航与Bug追踪Wiki.md) 的架构章节。*
