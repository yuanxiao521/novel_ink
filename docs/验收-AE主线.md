
# 验收 · 副驾/编排主线 A→E（可复跑清单）

> 版本 v1.0 · 2026-10-04 · 覆盖 **v1.18 → v1.24**
> 定位：把「编排层 + 场景层副驾」这条主线的验收动作固化下来——**每一批都有可复跑的命令**，UI 侧另给剧本式手动路径。
> 设计依据：[正文协作副驾与Agent协作架构.md](正文协作副驾与Agent协作架构.md)（A→E 已全部落地）

---

## 0. 版本台账

| 批 | 交付 | 版本 |
| --- | --- | --- |
| A | ToolRegistry + ToolExecutor（同一工具链）+ 责编对话 + 段落批注（迁移 0014）+ 按钮工具化 | v1.18 / v1.19 |
| A+ | 任务总线 `agent_tasks` / `agent_messages`（迁移 0015）+ **发起者硬边界** + 写权限矩阵草案 | v1.20 |
| B | 书级约束表**全员读**（0-token 派生，裁剪留痕）+ 统一 `perceive(scope)` + 一包多 persona 渲染 | v1.21 |
| C | 彩排**三方决策** + 素材使用率（启发式） | v1.22 |
| D | **审稿会**一次编排 + 评审给身份 + **约束进写手 prompt** + 按钮 7→3 | v1.23 |
| E | **角色在场感**（只读意见卡） | v1.24 |

---

## 1. 起服务（用户自启）

```powershell
# 1) 数据库（Docker Desktop 需先运行；容器随 Docker 启动会自动恢复）
docker start novel-ink-db
docker inspect --format "{{.State.Health.Status}}" novel-ink-db   # 期望 healthy

# 2) 后端（不要加 --reload：Windows 上曾卡死）
cd E:\novel_desk-agent\backend
& "E:\novel_desk-agent\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3) 前端
cd E:\novel_desk-agent\frontend
npm run dev        # 打开 http://127.0.0.1:5173

# 4) 迁移版本应为 0015
cd E:\novel_desk-agent\backend
& "E:\novel_desk-agent\.venv\Scripts\python.exe" -m alembic current
```

> 端口自检：`8000 / 5173 / 5433` 三个都应 LISTEN；`GET /health` 应返回 status=ok 且 degraded.active=false。

---

## 2. UI 手动验收（剧本式）

### 2.1 主线：从零到一章正文

| 步 | 在哪 | 操作 | 预期（能看出对错的检查点） |
| --- | --- | --- | --- |
| 1 | 侧栏 | 点「＋ 新书」→ 填书名 → **一句话方向可以留空跳过** | 新书出现在书架；若填了方向，设定页「方向」tab 能看到那一条 |
| 2 | 侧栏 / 上下文条 | 把「当前书」切到新书 | 全站同步（主笔页/导演台/正文协作/设定页都跟着换书）；**刷新不丢** |
| 3 | /maestro | 骨架区点「✦ 让主笔排骨架」 | 没方向会先问一句方向；随后出现**骨架预览卡** → 点「✓ 确认落库」→ 骨架树出现章/场景 |
| 4 | /characters | 点「✨ 主笔生成」 | 角色卡补齐（实测异能007 一次生成 5 张） |
| 5 | /maestro | 点某场景行 | 出现**节点详情抽屉**：场次/目标/舞台/彩排/素材/上场角色 + 「▶ 进入导演台 / ✎ 正文协作 / 配角色」 |
| 6 | /director（**可选**） | 选角 → ▶ 播放推演 | 回合推进；剧本台可看「▸ 思考」、✨高光可采纳。**这一步可以完全跳过** |
| 7 | /studio | 点「✍ 写手·初稿」 | 正文区出现初稿；**段落序号 ¶1 ¶2** 就位；审计出现写手记录 |
| 8 | /studio | 点「🗂 审稿会」 | 右栏自动切到「质检」并显示：**裁决** + 四步摘要；无问题时**润色会被跳过** |
| 9 | /studio | 点「💾 保存」 | **先弹确认**（破坏性操作闸门）→ 确认后落库 `scenes.final_prose`；审计多一条工具记录（发起 author） |
| 10 | /dashboard | 打开概览复盘 | 结构曲线/诊断读真实数据（有推演才有点；没有显示空态，**不是假曲线**） |

### 2.2 「导演台可选」怎么验

- **直接跳过**：第 3 步后不进导演台，直接进正文协作 → 写手照样能写（它有场景 4 字段 + 角色卡 + 世界状态 + **书级约束**）。
- **按需彩排**：/maestro 场景行 hover 出「◐ 彩排」→ 点它 → 去 /studio 右栏「**任务**」tab 能看到 `请求彩排`（状态 待受理），可推进「进行中 / 请作者确认 / 完成 / 失败」，**非法流转会被拒**。
- **建议从哪来**：新建一个含重头戏关键词的场景（如「祠堂前的决裂」），骨架行会出现 **◐ 建议排演**（悬停看理由）；**已排演过的场景不会再被建议**。

### 2.3 副驾与边界（最能看出"设计有没有落地"）

| 验什么 | 操作 | 预期 |
| --- | --- | --- |
| 责编对话走同一工具链 | /studio 右栏「责编」→ 输入"看看这段有没有 AI 味" | 回复下方出现**感知摘要**（场景/角色/批注/审计 + 裁剪数）与**执行结果**（✓ 工具名；✕ 带原因）；审计里能找到对应的 `工具` 记录 |
| 破坏性操作不可绕过 | 让责编"直接保存" | 它会说明需要作者确认；审计里不会出现偷偷落库 |
| 批注是"改稿清单" | 正文里**选中一句** → 浮动条「批注」→ 写意见 → 保存 | 段落下方出现批注卡（待处理）；左栏「待处理批注 ¶n」+1；点「标记已处理」→ 计数归 0、卡片转灰 |
| 批注能被 agent 读到 | 批注后再点「问责编」→ 发送 | 责编回复体现它读到了批注（感知摘要"批注"计数变化） |
| 角色在场 | /studio 右栏「角色」→「◐ 请角色说一句」 | 每角色一张卡：情绪 / 心里话 / **哪句不像我（逐字引用）** / 换成我会怎么说；标注 0-token 句长与语气词 |
| **角色不能发起任务** | 任务 tab | UI 没有"以角色身份发起"的入口；命令级验证见 §3.2（会 409） |
| **空 cast 不许推演** | 新书不配角色直接进导演台点播放 | 被拦下并指引「去配置上场角色」（不是静默空回合） |
| 降级必须可见 | `docker stop novel-ink-db` 后刷新页面 | 顶部出现红色**降级横幅**；`/health` 的 degraded.active=true；恢复 DB 并重启后端后消失 |

---

## 3. 命令级验收（可直接复制）

### 3.0 总闸门

```powershell
cd E:\novel_desk-agent
& "E:\novel_desk-agent\.venv\Scripts\python.exe" -m pytest backend/tests -q    # 期望 197 passed
cd frontend; npx tsc --noEmit; Write-Output "tsc exit=$LASTEXITCODE"               # 期望 exit=0
```

### 3.1 工具层与审计（A）

```powershell
$t = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/agent/tools"
"tools=" + $t.tools.Count                                    # 期望 16
"destructive=" + (($t.tools | Where-Object { $_.side_effect -eq "destructive" }).name -join ",")
```

### 3.2 发起者硬边界（A+ · 期望 409）

```powershell
$tmp = Join-Path $env:TEMP "t.json"
[System.IO.File]::WriteAllText($tmp, '{"kind":"rehearsal","from_agent":"character"}', [System.Text.Encoding]::UTF8)
try {
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/scenes/scene-betrayal-night/agent/tasks" -ContentType "application/json" -InFile $tmp | Out-Null
  "BAD: allowed"
} catch { "expect 409 -> " + $_.Exception.Response.StatusCode.value__ }
```

### 3.3 约束表全员读（B）

```powershell
$c = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/books/book-rain/constraints"
"source=" + $c.source + " counts=" + ($c.counts | ConvertTo-Json -Compress) + " dropped=" + $c.dropped.Count
"reasons=" + (($c.dropped | ForEach-Object { $_.src + "→" + $_.reason }) -join "；")   # 缺失项必须带原因
```

### 3.4 彩排建议与素材使用率（C）

```powershell
$p = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/books/book-rain/rehearsal-plan"
"scenes=" + $p.summary.scenes + " suggested=" + $p.summary.suggested + " rehearsed=" + $p.summary.rehearsed
$u = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/books/book-rain/material-usage"
"adopted=" + $u.summary.adopted + " rate=" + $u.summary.rate + " method=" + $u.method
```

### 3.5 审稿会（D · 真 LLM）

```powershell
$tmp = Join-Path $env:TEMP "m.json"
[System.IO.File]::WriteAllText($tmp, '{"args":{"text":"雨夜书房，烛火被风压弯。陈默把那张残页按在桌面，指节泛白。"},"confirm":false}', [System.Text.Encoding]::UTF8)
$m = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/scenes/scene-betrayal-night/tools/prose.review_meeting" -ContentType "application/json; charset=utf-8" -InFile $tmp
"verdict=" + $m.data.verdict
"steps=" + (($m.data.steps | ForEach-Object { $_.step }) -join "→")     # 无问题时：体检→质检→评审（跳过润色）
```

### 3.6 角色在场感（E · 真 LLM）

```powershell
$tmp = Join-Path $env:TEMP "v.json"
[System.IO.File]::WriteAllText($tmp, '{"args":{"text":"陈默低声道：原来如此。李文站在门边。"},"confirm":false}', [System.Text.Encoding]::UTF8)
$v = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/scenes/scene-betrayal-night/tools/character.voices" -ContentType "application/json; charset=utf-8" -InFile $tmp
"opinions=" + $v.data.opinions.Count + " note=" + $v.data.note
$v.data.opinions | ForEach-Object { $_.name + " | " + $_.emotion + " | 句长=" + $_.avg_len }
```

### 3.7 降级可观测（T9）

```powershell
(Invoke-RestMethod "http://127.0.0.1:8000/health") | ConvertTo-Json -Compress
# 期望 status=ok 且 degraded.active=false, count=0
```

---

## 4. 存量验收（S1–S4 / A2 / A3，仍在跑）

```powershell
cd E:\novel_desk-agent
$py = "E:\novel_desk-agent\.venv\Scripts\python.exe"
& $py backend\scripts\accept_s2.py  --report docs\验收-S2口吻-原始输出.txt
& $py backend\scripts\accept_s3.py  --report docs\验收-S3张力-原始输出.txt
& $py backend\scripts\accept_s4.py  --report docs\验收-S4剧本-原始输出.txt
& $py backend\scripts\accept_a3.py  --report docs\验收-A3反AI味-原始输出.txt
& $py backend\scripts\accept_a2.py  --report docs\验收-A2质量回环-原始输出.txt
# 期望 11/11 · 9/9 · 15/15 · 17/17 · 13/13（加 --live 会真调 LLM）
```

---

## 5. 已知未做 / 仍是雏形（诚实清单）

| 项 | 现状 |
| --- | --- |
| **写权限矩阵强制** | 只出草案（`agents/permissions.py`：12 条字段级 owner，enforced=false），未在写入路径启用 |
| **Redis** | 未引入（触发条件：多 worker / 后台长任务 / 多人实时 / 高频进度）；纪律：只做传输·缓存·锁，不放业务事实 |
| **素材库（RAG）** | 侧栏灰态占位「RAG 后」；属 A1 上下文 + 跨书素材，等触发 |
| **成书 / 导出** | 正文协作可「导出剧本」（场景级）；**章节正文组装成书**仍是雏形 |
| **世界状态浏览视图** | 设定页「世界状态」是诚实空态：账本已落库，缺只读列表接口 |
| **A4 分批交付** | 主笔仍一次性 commit 骨架（分批复核未做） |
| **候选池 T1–T8** | 仍待考虑（场景快照 / 文学减法 / 主笔回溯 / 叙事选择 / 角色非理性授权 / 角色卡扩展 / 事实硬闸门 / 四类约束表） |
| **README** | 未写（作者明确"项目还没完全做好"） |

---

*本清单 v1.0；后续每批落地按同一格式追加「命令级验收」段落，并同步 Wiki §0 版本历史。*
