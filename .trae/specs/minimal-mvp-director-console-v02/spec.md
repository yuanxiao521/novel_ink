# 最小 MVP 闭环 · 剧本演绎导演台 Spec（v0.2）

> 版本：v0.2（承接 v0.1 后端五层重构 + 最小导演台）
> 状态：DRAFT（待用户 Approval 后进入 apply）
> 目标：把「涌现式小说 Agent」从"后端可跑"推进到"浏览器里能看完整发挥→收敛闭环的导演台"。

## Why

v0.1 已完成：后端五层架构、LangGraph 单回合循环、无 Key 确定性回退、最小 API（start/step/state/intervene）、真实 PG 持久化。但**前端的导演台（`webapp/index.html`）还是纯静态假数据**，没有接通后端；且角色决策、导演计划全是硬编码脚本，**从未接入真实 LLM**，因此看不到"角色自主思考、涌现出剧情"的真实效果。

本轮要完成"最小 MVP 闭环"：**接真实 LLM 驱动角色/导演 + 后端补角色思考层 + SSE 流式推送 + 前端导演台接通真实 API 并实时渲染**，让作者在浏览器看到一场戏从角色各自思考、行动、冲突到收束的完整过程。

## What Changes

- **新增 LLM 调用层**（此前完全没有实际 LLM 调用）：
  - 强模型（`model_strong`）→ 角色演绎：感知 → 思考 → 决策
  - 廉模型（`model_cheap`）→ 导演调度 / 张力评估 / 成文 / 一致性
  - OpenAI 兼容协议；无 Key 时自动回退现有确定性脚本（保住 no-key 可跑）
- **角色思考层（④角色卡 prompt 分层）**：`perceive_context` → 思考（内心独白/推理）→ 决策（行动+台词+可选 new_fact），中间产物可被 SSE 推送。
- **后端新增 SSE 端点**：`/sims/{id}/stream`，流式推送每个角色的感知/思考/决策/事件/记忆 阶段增量。
- **导演台前端接通真实 API**：`webapp/index.html` 由静态假数据改为 `EventSource` 消费 SSE，实时渲染角色栏/世界黑板/导演控制台/时间线/成文。
- **数据模型扩展**：`CharacterCard` 增加可编辑的演绎 prompt 模板字段（对齐 `docs/prompt核心设定.md` ④层，前端人物页可改，本轮仅数据层）。
- 版本管理：spec 遵循 v0.2，依赖 v0.1 已完成的地基，不重造。

> 明确沿用 v0.1 的无 Key 兜底：非「必须有 LLM」的硬依赖，无 Key 也全程可跑、可演示（只是看到的是确定性脚本行为）。

## Impact

- Affected specs：
  - v0.1 `backend-layered-refactor-v01`（地基，全部复用）
  - `docs/prompt核心设定.md`（④角色卡 prompt 分层，本轮落实数据字段）
  - `docs/MVP设计.md`（§2 全景 / §5.2 图 / §4.1 角色卡）
- Affected code：
  - 后端：`app/services/engine/character.py`、`director.py`、`graph.py`、`world.py`、`service.py`、`schemas/models.py`、`config.py`、`api/routers/simulation.py`；新增 `app/services/llm/`（LLM 客户端层）
  - 前端：`webapp/js/app.js`（消费 SSE 渲染）、`webapp/index.html`（结构预留）、`webapp/css/*`（沿用基调）
  - 依赖：`pyproject.toml`（加 openai 官方 SDK 或 httpx）

## ADDED Requirements

### Requirement: LLM 调用层
系统 SHALL 提供 OpenAI 兼容的 LLM 客户端，支持强/廉模型分工，且无 Key 时回退确定性脚本。

- **WHEN** `NOVEL_OPENAI_API_KEY` 配置且 reachable
- **THEN** 角色演绎走强模型、导演/成文/评估走廉模型，产出真实 LLM 文本
- **WHEN** 未配置 key 或调用失败
- **THEN** 自动回落现有 `decide_fn`/`converge_fn`/确定性回退，不中断闭环

### Requirement: 角色思考层（感知→思考→决策）
系统 SHALL 让每个角色先产出内心独白（思考）再产出行动决策，中间产物可见、可推送。

- **WHEN** 角色进入行动轮次
- **THEN** 先输出"感知上下文"，再输出"思考/理由"，再输出最终"行动+台词+可选 new_fact"
- **AND** 上述阶段各自成为可独立推送的 SSE 阶段（perceive/think/act）

### Requirement: SSE 导演台端点
系统 SHALL 提供 SSE 流式端点，实时推送单回合内的逐阶段增量。

- **WHEN** 客户端订阅 `/sims/{id}/stream`
- **THEN** 按 感知→思考→决策→事件→记忆→成文 顺序逐条推送每个事件
- **AND** 回合推进、张力、举手、护栏状态随事件流实时下发

### Requirement: 前端导演台接通真实 API
系统 SHALL 让 `webapp/index.html` 导演台从静态假数据改为消费播放 SSE 的真实数据。

- **WHEN** 作者在导演台点"开始/播放"
- **THEN** 前端用 EventSource 订阅 stream，实时渲染角色栏（思考/行动）、世界黑板（环境事实/事件）、导演控制台（张力/提示/举手/护栏）、时间线（回合）、成文预览
- **AND** 渲染沿用纸墨+mood 基调（`docs/prompt核心设定.md` / `webapp/css/tokens.css`）

### Requirement: 角色卡可编辑演绎模板字段
系统 SHALL 在 `CharacterCard` 增加可编辑的演绎 prompt 模块字段（对齐 ④ 层），数据层可写。

- **WHEN** 作者在人物页修改角色演绎偏好/性格/腔调/决策偏好
- **THEN** 后端数据模型能读写这些字段（本轮仅数据层，UI 另算）
- **AND** 运行时感知拼装时按该角色模板 + ③环境感知 + ②导演暴露生成 prompt

## MODIFIED Requirements

### Requirement: 角色决策从脚本→LLM（可回退）
原 `decide_fn`（场景内硬编码脚本）升级为"LLM 演绎为主、脚本兜底"。

- **LLM**：按 ④ 层 prompt（角色卡模板 + 感知注入 + 导演暴露）生成思考+行动
- **回退**：LLM 不可用时仍可调用原 `decide_fn`，保证 no-key 闭环稳定

### Requirement: 导演计划从确定性→LLM（可回退）
原 `fallback_plan`（确定性策略）升级为"LLM 调度为主、确定性兜底"。

- **LLM**：读顶层设定 + 全量黑板 + 信念账本全貌，出软引导（注入/曝光/调权）/ 张力 / 举手 / 收束
- **回退**：LLM 不可用时回落原 `fallback_plan` + `converge_fn`

## REMOVED Requirements

（无移除。v0.1 无 Key 兜底机制保留，作为本轮 LLM 不可用时的回退路径。）

---

## 版本管理约定
- 本文件即 v0.2；专属 spec 层面沿用 `backend-layered-refactor-v01` 的"v01 目录"命名习惯。
- 后续开发阶段通过 `tasks.md` 勾选进度、`checklist.md` 逐条验收，实时反映"开发到哪一步"。