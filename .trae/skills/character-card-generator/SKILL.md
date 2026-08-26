---
name: "character-card-generator"
description: "Generates a five-dimension CharacterCard with Soul Field from a one-line persona. Invoke when creating or expanding a character for the simulation."
---

# Character Card Generator (角色卡生成器)

把一句话人设扩成结构化 `CharacterCard`（对齐 `schemas/models.py`），供角色引擎 `_compose_prompt` 使用。

## 五维设计（对齐角色卡 schema）

1. **人设 summary**：一句总述（核心矛盾 + 行动风格）
2. **人格特质 traits**：3-5 条，具体可演（"嘴硬心软"优于"善良"）
3. **说话腔调 voice**：句式/用词/口语习惯，决定演绎时对白辨识度
4. **核心信条 core_beliefs**：角色认死理的 1-3 条（信念账本的精神根）
5. **性格底线 bottom_lines**：护栏判 OOC 的依据——写出"绝不做什么"

## Soul Field（灵魂场，story-craft 技法移植）

每个角色一个"角色内核矛盾"：他的**欲求** vs **恐惧** vs **代价**。
例：欲求"被父亲认可" × 恐惧"成为父亲那样的人" × 代价"正在变成他"——这三角是持续张力的发动机，写入 summary 与 dynamic_goals。

## 动态目标 dynamic_goals（可被导演调权重）

每个目标带 `weight`（0-1）与 `last_adjust_reason`（导演调权重必填剧情内因）。
2-3 个互相拉扯的目标最佳（如"守护妹妹" vs "证明自己"）。

## ④层演绎字段（作者可在前端人物页改，空则用引擎默认）

- `system_prompt`：作者对演绎的额外要求（占位 `{self}` 可引用角色本人信息）
- `think_schema` / `decide_schema`：思考/决策 JSON 输出约束
- `static_world`：该角色可读的静态设定（世界观/人物关系，书中层未建立前的载体）

## 输出 JSON 示例

```json
{
  "id": "char_001", "name": "陈默",
  "summary": "雨夜归家的落魄执笔人，怀疑发小背叛，又在等一个解释。",
  "traits": ["话少", "藏怀疑", "行动先于追问"],
  "voice": "短句，叙述多于抒情，问句总在句尾",
  "core_beliefs": ["人一旦说谎，眼神会先出卖自己"],
  "bottom_lines": ["不会物理伤害李文", "不会直接翻保险柜"],
  "dynamic_goals": [ { "id": "g-defend", "text": "确认发小是否背叛", "weight": 0.7 } ],
  "soul_field": "欲求：被信任 × 恐惧：信错人 × 代价：疑心正在毁掉旧情"
}
```

## 参考

- `docs/prompt核心设定.md` ④角色卡层
- `docs/agent职责与prompt设计.md` §4