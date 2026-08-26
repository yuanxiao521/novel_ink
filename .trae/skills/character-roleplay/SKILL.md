---
name: "character-roleplay"
description: "Role-plays a character inside the simulation: perceive own sub-context, think briefly, decide one action. Invoke per character per turn by the character engine."
---

# Character Roleplay (角色演绎)

角色是"演员"：在自己的感知子集 + 信念账本里涌现言行。**角色决策是黑盒**——引擎只负责供料和护栏，不干涉角色怎么想。

## 四层注入（prompt 组装顺序，只读自己的子集）

1. **作者腔调**（character.system_prompt，④层，空则跳过）
2. **世界观设定**（static_world / ①书层裁剪到该角色可知）
3. **感知现场**（③环境感知层：`visible_to(char_id)` 裁剪的 facts + env + 近因事件）
4. **信念账本**（自己那份 Belief：亲见/被告知/推测 + 溯源）
5. **导演曝光**（导演目标调整伴随的剧情内因——角色必须'知道自己为什么变了'）

**禁止**：读全量黑板、读他人信念、把导演话术当自己的动机（铁律#1：角色说不出"导演让我这么做"）。

## 思考（think）—— 短而真

思考是决策的前置内心独白，控制在 2-3 句（思考过长挤压成文，已定调收短）。输出：`analyze`（怎么看待现状）+ `emotion`（情绪水位）。

## 决策（decide）—— 一个行动

```json
{ "actor": "char_001", "kind": "dialogue|action|conflict", "text": "做了什么/说了什么", "new_fact": "（可选）要确立的新世界事实" }
```

- 决策按导演指定的行动顺序**串行**执行：后手角色能看到先手角色刚做的行动（近况感知）
- `new_fact` 会过世界事实护栏（矛盾/查重），不合规矩会被打回重演（最多 2 次熔断）

## 演绎硬约束

- 不打破第四面墙：不知道"这是小说/导演/作者"
- 情绪和人设一致（persona_guard 底线校验）
- 世界观规则不可违背（境界限制等，S4 起有规则校验器）
- 用角色自己的话术，不出现通用 AI 腔（"让我们面对现实"这类）

## 参考

- `docs/prompt核心设定.md` ③环境层 + ④角色卡层
- `docs/agent职责与prompt设计.md` §4