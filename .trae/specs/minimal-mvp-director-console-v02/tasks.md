# Tasks（v0.2 · 最小 MVP 闭环·剧本演绎导演台）

> 有序小步、每步可验证、有用户可见进展。完成一项勾一项。

- [x] Task 1: 数据模型扩展：`CharacterCard` 增加可编辑演绎 prompt 模板字段（④层）
  - [x] `schemas/models.py` 的 `CharacterCard` 增加 `system_prompt` / `think_schema` / `decide_schema` 等可编辑模板字段（默认空 → 用内置默认）
  - [x] 保证 `model_dump/model_validate` 向后兼容（旧快照可加载）
  - [x] 验证：`uv run pytest` 既有测试不回归；新增一条卡字段读写断言

- [x] Task 2: LLM 调用层（强/廉模型分工 + 无 Key 回退）
  - [x] 新建 `app/services/llm/client.py`：OpenAI 兼容 `chat/completions`（httpx 或 openai SDK），封装 `call_strong(prompt, json_schema?)` / `call_cheap(...)`
  - [x] 无 key / 调用异常 → 返回 None（让调用方走回退脚本），不中断
  - [x] `config.py` 确认强/廉模型字段可被 `call_*` 使用
  - [x] 验证：无 key 时 `uv run python -c` 调用返回 None 不抛；有 mock 的 httpx 时返回结构化 JSON

- [x] Task 3: 角色思考层（感知→思考→决策）+ LLM 演绎 + 脚本兜底
  - [x] `character.py` 新增 `think()`（LLM 产内心独白/推理，结构化 JSON）与 `decide()`（LLM 产行动+台词+new_fact）
  - [x] LLM 不可用 → 回落现有 `decide_fn`（保留视角隔离与护栏）
  - [x] 思考/决策中间产物写入临时状态，供 SSE 与后续护栏使用
  - [x] 验证：无 key 走脚本闭环原样跑通；mock LLM 时产出思考+行动

- [x] Task 4: 导演 LLM 调度 + 确定性兜底
  - [x] `director.py` 新增 `llm_plan()`：读顶层设定+黑板+信念全貌，出 软引导/张力/举手/收束
  - [x] LLM 不可用 → 回落 `fallback_plan` + `converge_fn`
  - [x] 验证：无 key 回退原样；mock LLM 出软引导（注入/曝光/调权带内因）

- [x] Task 5: SSE 端点 `/sims/{id}/stream`
  - [x] `api/routers/simulation.py` 新增 SSE 流式端点，逐步推进回合并推送 感知→思考→决策→事件→记忆→成文 分段事件
  - [x] 复用 LangGraph 单回合循环，但改为"node 级可推送"或"分阶段 yield"
  - [x] 每回合结束推送剧情摘要（pending 版本约定为 JSON Lines 格式≥1种：`event:`/`data:`）
  - [x] 验证：`curl -N` 或 TestClient 能收到多个 event；无 key 也能流式推送脚本行为

- [x] Task 6: 前端导演台接通 SSE 真实 API
  - [x] `webapp/js/app.js` 用 `EventSource`（或 fetch README stream）消费 `/sims/{id}/stream`
  - [x] 角色栏 / 世界黑板 / 导演控制台 / 时间线 / 成文 各模块用 SSE 数据替换硬编码假数据
  - [x] 纸墨 + mood 基调沿用（`tokens.css`/`app.css`），不另起画风
  - [x] 验证：起后端 + 打开 `index.html`，点"开始"能逐步看到角色思考→行动→张力→收束

- [x] Task 7: 端到端验证 + CORS + 静态服务
  - [x] 确认后端 CORS 放行 `webapp` 来源；提供静态文件服务或说明前端启动方式
  - [x] 用测试/脚本跑通"start → stream 全部回合 → 收敛/举手"，断言事件日志、信念溯源、持久化
  - [x] 更新前文装有 key 的演示路径说明

# Task Dependencies
- [Task 2]（LLM 层）依赖 [Task 1]（模型字段，供 prompt 模板）
- [Task 3][Task 4] 依赖 [Task 2]
- [Task 5]（SSE）依赖 [Task 3][Task 4]（角色/导演 LLM 已就位，能产出可推送的分阶段事件）
- [Task 6]（前端 SSE）依赖 [Task 5]
- [Task 7]（端到端）依赖 [Task 5][Task 6]
- Task 1 — Task 4 可部分并行（模型字段先行；LLM 层/角色/导演可独立开发后联调）