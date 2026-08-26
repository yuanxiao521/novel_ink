---
name: "tension-assessment"
description: "Independently assesses narrative tension and divergence on a 0-100 scale from conflict frequency, turn pacing and goal-weight clashes. Invoke for objective tension scoring where the director must not self-evaluate."
---

# Tension Assessment (张力场判定)

独立评估器：导演**不自评**（MVP §6.2「张力不导演自评」）。本 skill 给出统一口径的张力/divergence 判定，供导演决策、收束汇报与前端展示。

## 评估维度（每维 0-20，总分 0-100）

| 维度 | 看什么 |
|---|---|
| 冲突密度 | 距最近冲突事件的回合数 + 冲突事件类型分布（对话/行动/矛盾） |
| 节奏 | 回合步进快慢、换场频率、张力是否高平/骤降 |
| 目标对峙 | 在场角色 dynamic_goals 的权重冲突度（互相抵消的目标=张力源） |
| 信息差 | 信念账本可见差（谁知道谁不知道）被点破的程度 |
| 收束压力 | 距结局集合的接近度（太近=将收，太远未埋线=将散） |

## 输出

```json
{ "tension": 62, "trend": "up|down|flat", "divergence": "low|medium|high", "reason": "一句中文归因" }
```

## 建议档位（供导演借用）

- `tension < 30`：无戏，导演出事件注入推一把
- `30-70`：健康区间，软引导维持
- `> 70`：张力硬顶，警惕台词土崩——导演改曝光/收束方向
- 连续 2 回合 flat 且低：导演应举手或注入新事件，禁止硬拖

## 规则

- 只评估不决策：给出数字与归因，不产出情节
- 用廉价模型（flash）批量评估，回合内不阻塞
- 可用确定性启发式代理（距冲突回合数/类型分布）兜底，无 LLM 也出分

## 参考

- `docs/MVP设计.md` §6.2（独立评估/启发式代理指标）
- `docs/agent职责与prompt设计.md` §3.2