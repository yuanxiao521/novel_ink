# 优化方案 A3 · 反 AI 味可编程校验器（Anti-AI-Tone Validator）

> 版本 v0.1 · 2026-09-13
> 上位文档：`docs/写作痛点清单与优先级.md`（A3 · AI 腔同质，等级 A）
> 定位：把"别写 AI 腔"从**口号**变成**可编程的确定性拦截 + 定点重写 + 复检回环**。
> 关键前提：**底座已就绪** —— S2 step5 已交付 `engine/style_checks.py::ai_tone_scan()`（套话表/破折号/省略号/填充词密度 + 阈值 + 证据片段），当时即按"A3 共用"设计。A3 不造第二套校验器。

---

## 1. 现状盘点（真实代码对照）

| # | 事实 | 证据 | 影响 |
| -- | -- | -- | -- |
| D1 | **无确定性校验器** | 全后端 grep：反 AI 味只存在于 prompt 措辞（`prose.py::_POLISH_PROMPT`"去掉'然而/顿时/仿佛'等套话"、`_DRAFT_PROMPT` 口吻纪律） | 全靠模型自觉；温度一抖就复发，且**不可测、不可拦** |
| D2 | 底座已存在但只做"信号" | `style_checks.ai_tone_scan()` 返回 `cliches`（含 count + evidence 片段）、`dash`/`ellipsis` 密度、`filler_per_100`、`flags`；阈值 `CLICHE_TOTAL=6`/`DASH_PER_1000=6`/`ELLIPSIS_PER_1000=6` | 体检 prompt 能引用它，但**没有"拦截"与"定点重写"** |
| D3 | 纠错通道已存在但只兜结构 | `engine/schema_retry.py::validate_and_retry`（校验→反馈重试→熔断），当前接入点：plan/cards/rules/正文结构化产出 | 内容级（文风）问题进不了这条通道 |
| D4 | 润色师是**全量**润色 | `polish_prose()` 对全文改写，只给 `summary ≤120 字` | 命中 1 处也要全文重写 → 高风险（易改坏事实/口吻），且无法定点 |
| D5 | 缺"改对了吗"的复检 | 润色后没有二次 scan | 可能越改越糟而无感知 |
| D6 | 实测真实文本的 AI 味信号**确实存在** | S3/S2 实测：真 LLM 初稿 254 字破折号密度 **11.8/千字**（阈值 6）→ 被标红 | 规则不是空想，是真实命中 |

> 参考做法（痛点清单 §3 A3）：InKOS 11 条确定性规则 + spot-fix；long-novel-agent 高频词审计。

---

## 2. 目标

1. **规则集扩展**（0-token，全部带证据片段）：套话表、破折号/省略号密度、填充词密度（已有）+ **新增**：§3.1 的 R-A3-1~R-A3-6；
2. **分级**：`violation`（硬拦）/ `warning`（提示），与世界观规则同一套 severity 语义；
3. **定点重写（spot-fix）**：只重写命中句，不全文重写；输出 `after` + "改了什么/为什么"；
4. **事实不变闸门**（0-token）：重写后**专名/数字/术语/引号内台词必须逐字保持**，否则放弃该次重写；
5. **复检回环**：重写后重新 scan，命中数必须**下降**才采纳，否则回退原文；
6. **落点**：`prose_notes.payload_json.ai_tone`（复用迁移 0010，**不新增表**）+ 可复跑验收脚本 `scripts/accept_a3.py`。

---

## 3. 设计

### 3.1 规则集（新增 6 条，与既有 3 条并列）

| 规则 | 判定（0-token） | 级别 |
| -- | -- | -- |
| R-A3-1 套话表 | `CLICHES` 命中总数 ≥ **按字数归一**的阈值（每千字 6，短文本地板 3）—— 审计发现原计划写"默认 6"是纯绝对值：长章必然命中、短句几乎不可能命中，两端都错 | warning |
| R-A3-2 破折号/省略号滥用 | 密度 > 6/千字（既有） | warning |
| R-A3-3 填充词密度 | `了/是/的/就/都` 密度 > 阈值（既有 `FILLER_PER_100`） | warning |
| **R-A3-4 段落收尾升华** | 段末句命中抽象情绪/总结词表（"明白了/他知道/这一刻/终究/或许/仿佛一切…"）且句长 < 阈值 → 典型"每段结尾拔高" | warning |
| **R-A3-5 同构句排比** | 连续 ≥3 句**结构同构**（长度差 ≤2 且首二字相同/句式模板相同）→ 工整假流畅 | warning |
| **R-A3-6 高频实词集中** | 全文实词（去停用词，2-gram 近似）Top-N：出现 ≥5 次且占比 ≥8% → 词穷式复读。**必须传 `exclude`（角色名 + 世界状态术语）**：审计实测——正常叙事里主角名「林尘」出现 8 次（96 字占 10%）会被判 violation；若不排除，spot-fix 会去"修"主角名 | violation |

- 阈值集中在 `style_checks.py` 顶部常量（便于实测调参）；
- **单条命中不改文**，只是"点"；改文交给 spot-fix。

### 3.2 定点重写（spot-fix）

```
输入：原文 + scan 命中（含 evidence 片段与句子定位）
LLM：只对命中句重写，约束 = ①保持事实/专名/数字/术语逐字不变 ②保持角色腔调（复用 S2 的卡注入）
输出：{after_sentence, changed, reason}（结构化，走 validate_and_retry）
0-token 闸门（输入明确化）：①角色名集合（场景卡）②数字/时间正则 ③world_states 的 term/numeric 键名与值 ④全部引号内台词 → 逐字比对，任一被改动即丢弃该次重写（宁可不动）
复检（双闸）：①ai_tone 命中数必须下降 ②**口吻不劣化**（复用 S2 的 voice_prior：角色句长/语气词集合不应剧烈变化），任一不满足 → 回退原文并报告"改不动"
```

### 3.3 与既有角色的边界（与 S2 一致）

| 角色 | 职责 | 本方案改动 |
| -- | -- | -- |
| 写手 draft | 出初稿 | 不动（初稿本就该自由） |
| 体检员 review | 对照角色卡评口吻 | 已引用先验；A3 的 `ai_tone` 一并进体检 prompt（增量 1 行） |
| 润色师 polish | 去 AI 味润色（全量） | **保留**（作者手动全量润色仍有用）；A3 提供 `spot_fix` 作为"定点版" |
| 质检员 verify | 对照账本质检 | 不动 |
| **新增 `ai_tone` 检查 + `spot_fix`** | 确定性拦截 + 定点重写 | `engine/ai_tone.py`（新）+ 2 个接口 |

### 3.4 API（并增）

- `POST /scenes/{id}/prose/ai-tone` → `{report: {rules, flags, counts}, note_status}`（扫描 + 落 editor note，payload 存 `ai_tone`）；
- `POST /scenes/{id}/prose/spot-fix` → `{after, changes:[{before, after, rule, reason}], report_after, accepted}`（定点重写 + 复检，落 polisher note）。

### 3.5 前端（并入现有正文协作面板）

StudioPage/SceneStudioPanel 增「AI 味」区块：规则命中清单（带证据片段）+ 「定点修复」按钮（只显示被改句子 diff）；**不做**一键全文重写。

---

## 4. 改动文件清单

| 文件 | 改动 | 风险 |
| -- | -- | -- |
| `backend/app/services/engine/style_checks.py` | 增 R-A3-4/5/6 规则 + `ai_tone_report()`（聚合 + 句子定位） | 低（纯函数，可单测） |
| `backend/app/services/engine/ai_tone.py` | **新增**：spot-fix（LLM 结构化）+ 事实不变闸门 + 复检回环 | 中（prompt + 闸门） |
| `backend/app/services/service.py` | `prose_ai_tone` / `prose_spot_fix`（落 note + payload） | 低-中 |
| `backend/app/api/routers/simulation.py` | 2 个并增端点 | 低 |
| `backend/tests/test_ai_tone.py` | **新增**：6 条规则 + 闸门 + 复检 + 服务/接口 | 低 |
| `frontend/src/components/backoffice/SceneStudioPanel.tsx` + `api/novel.ts` | 「AI 味」区块 + 定点修复 | 中（前端） |
| `docs/写作痛点清单与优先级.md` / Wiki | A3 现状与版本记录 | 低 |

---

## 5. 验收标准（可测）

1. **规则可复现**：构造文本命中 R-A3-1~R-A3-6 各≥1 条，且干净文本 **0 命中**（不误伤）；
2. **事实不变闸门**：重写句把"林尘"改成"林凡"或把"第三日"改成"次日" → 该次重写被**丢弃**；
3. **复检回环**：重写后命中数未下降 → 回退原文并标 `accepted=false`；
4. **落库契约**：note 的 `payload_json.ai_tone` 含 rules/flags/evidence；
5. **回归**：全量 pytest 全绿；S1/S2 行为不回退；
6. **真数据**：对 DB 里真实初稿跑 scan，命中项与人工阅读体感一致（S2 已实测破折号超阈值）；
7. **专名不误报**（审计新增）：主角名高频出现的正常叙事 → R-A3-6 **0 命中**（传 exclude）；不传时 detail 必须诚实标注"可能误判专名"；
8. **阈值归一**（审计新增）：同样 3 次套话 → 短句命中 R-A3-1、长章（约 800 字）不命中。

---

## 6. 明确不做 / 触发条件

| 不做 | 原因 | 触发何时纳入 |
| -- | -- | -- |
| 自动全文重写 / 无条件改写 | 破坏作者掌控（A4 红线） | 定点修复采纳率长期 >80% 时再评估 |
| 用 LLM 做"AI 味评分" | 确定性规则已够、可解释、免费；LLM 自评不稳定 | 规则与实际体感偏差大时 |
| 风格模仿（"像海明威"） | 属 T4 待考虑项（叙述层叙事选择），不在 A3 | 作者明确要风格化时 |
| 词表无穷扩充 | 维护成本高 | 出现规则未覆盖的高频套话时按证据补 |

---

## 7. 推进步骤（一次只走一步）

1. ✅ **勘察现状 + 出本方案**（本次；关键发现：底座已就绪、缺拦截与定点修复、D6 有实测量化证据）
2. ✅ **（含审计修复：专名排除 + 阈值归一，见 §5-7/8）** 规则扩展 + `ai_tone_report()` + 单测：R-A3-1/2（既有信号升级）+ **R-A3-3 填充词**（修「声明了却不可达」）+ R-A3-4 段末拔高 + R-A3-5 同构排比（同首字 + 句长差 ≤2，防误伤）+ R-A3-6 高频实词（violation）；`test_ai_tone.py` 7 例（含干净文本 0 命中）
3. ⬜ spot-fix（结构化重写）+ 事实闸门 + 复检回环 + 单测
4. ⬜ 服务 + 2 个并增端点 + 接口测试
5. ⬜ 前端「AI 味」区块 + `tsc` 零错误
6. ⬜ 真数据验收 + `scripts/accept_a3.py` + 文档归档（痛点清单 A3 现状、Wiki 版本行、§11）

---

## 8. 关联文档

- 上位：`docs/写作痛点清单与优先级.md`（A3；"把反 AI 味升级成可编程校验器，接 `schema_retry.py` 纠错通道"）
- 共用底座：`docs/写作优化方案-S2角色口吻.md` §3.5（`style_checks.py`：S2 口吻先验 + A3 反 AI 腔同源）
- 同套路：S1（并增 schema 不新增表）、S3（零新表纯派生 + 可复跑验收脚本）
