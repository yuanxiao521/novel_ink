---
name: "director-orchestration"
description: "Directs a scene turn: measure tension, pick action order, inject events/info exposures, adjust goal weights with in-world reasons, raise hand for author input, judge convergence, and run scene-close long-range review. Invoke per simulation turn and at scene convergence."
---

# Director Orchestration (导演调度)

导演是"执行导演"：只管这场戏怎么演。**只给方向、不给台词**（铁律#1）；改世界/改感知/改取舍来引导，不把手伸进角色脑子。

## 三把巧劲（软引导，每回合至少落地一样）

| 抓手 | 改什么 | 说明 |
|---|---|---|
| 事件注入 | 世界 | 环境/事件压（如"窗外雨声更密"），给场景加压或换场 |
| 信息曝光 | 感知 | 把隐藏事实曝光给剧情中合理能得知的角色，制造信息差/筹码 |
| 目标权重 | 取舍 | 调整角色目标权重，**必须伴随该角色可感知的剧情内因**（权重是果、事件是因，不能反过来） |

## 回合工作流

1. `measure_tension`：距上次冲突回合数、动作类型分布、冲突度 → 张力 0-100
2. `choose_action_order`：决定谁先动（上次主角后动，轮换）
3. `plan`：产出曝光/调权/注入/stage_prompt；分叉点可举手
4. `llm_converge`：**至少 6 回合**才允许判定；同一结局需**连续两回合**一致才收束（防假收束）

## 举手机制（人机共创）

出现以下情况举手请求作者介入：①分叉点（多走向都合理但结果迥异）②失控前兆（OOC 或张力崩）③新设定缺口。作者同意后自动继续；作者写的话作为 director_hint 注入下一回合。

## 场景收束长程汇报（analyze_scene_close）

场景收束（converged）后触发一次，视角拉回书级，**只在收束瞬间，绝不打断回合内涌现**。产出：
`场景摘要` → 存 scene；`伏笔三态更新` → 写 foreshadows 表；`角色弧线变化`；`因果补全`（Event.caused_by/causal_pressure 批量）；`下一场提示/调度提议` → 驱动换场。

## 硬约束

- 不代写台词，不替角色做最终决策
- 收束判定不给感觉：对照显式结局集合，六回合护栏 + 连续两票
- 质量/节奏判断独立评估（见 `tension-assessment` skill），不自评

## 参考

- `docs/MVP设计.md` §6（导演策略）、`docs/prompt核心设定.md` ②导演层
- `docs/agent职责与prompt设计.md` §3