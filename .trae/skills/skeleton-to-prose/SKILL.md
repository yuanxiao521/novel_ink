---
name: "skeleton-to-prose"
description: "Renders a simulation event flow into novel prose with narration, scenery, action and psychology in an anti-AI-flavor style. Invoke when converting turn events into the novel text shown to the author."
---

# Skeleton To Prose (骨架→正文)

成文是"渲染器"：事件流（骨架）→ 小说正文。**不改变剧情事实，只决定怎么写**（MVP 铁律#3 模拟层与成文层分离）。

## 输入

本回合事件流：角色行动/对白/导演注入/环境变化（结构化，含 actor/kind/text）。可选配合：场景 `stage_desc`（舞台布置）、`env_conds`（环境条件）、世界规则（避免渲染出设定硬伤）。

## 输出

一段或数段小说正文（旁白体），中文小说语感。对话用「引号 + 说话人」衔接，不用剧本标签。

## 渲染手法（story-craft 反 AI 味清单移植）

- **环境/景物**：用物件与天气说话（"雨声渐密"而非"气氛紧张"）
- **动作留白**：用动作代替形容词（"他把手按在封皮上，指节泛白"）
- **心理细节**：内心独白只给线索，不说破全部
- **对话分层**：各角色语气不可混同（对应角色 voice）
- **张力分层**：平静段短句收束、冲突段节奏加快、高潮段留白
- **拒绝 AI 味**：不用"让我们面对""仿佛置身""换言之"；少用四字词堆砌；不用"然而、旋即、不禁"高频滥用词

## 玄幻渲染约定

- 打斗呈现招式/灵力消耗/攻防距离/空间感，不写"他打了她一拳"
- 境界/规则只在角色已知范围内出现，不剧透世界观
- 旁白可承担"摄像机"（叙述环境与态势），但不得说出角色不知道的信息

## 硬约束

- 不得新增事件流之外的剧情事实，不得让角色说出没发生的台词
- 事件里是 `dialogue` 就写对话；是 `action` 就写动作与场面
- 导演注入的环境变化必须进入正文（如"窗外雨声更密了"必须在渲染里可见）

## 参考

- `docs/agent职责与prompt设计.md` §5（成文 prompt 骨架）
- story-craft 技法仅作内部规则参照，运行时不依赖插件