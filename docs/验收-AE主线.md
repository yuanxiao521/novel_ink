
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
| F | **长任务后台化**（arq 队列，刷新/断网不断链）+ 审稿会并发提速 + **按钮原位、过程与明细进责编流**（SSE 过程卡 + 明细富卡 + 审计可收起） | v1.41 / v1.42 / v1.43 |
| G | **写手素材治理**（账本筛选/去重/脏名字闸门 + 场景缺口显式标注）+ **正文 token 流式** + 脏缓冲保护 | v1.44 |
| H | **队列活性可见**（`/health.queue` 心跳）+ worker 防抖监督 + 启动自动清扫僵尸任务 | v1.45 |
| I | **直写链路**：章成为最小可写单位（`chapters.prose_md` + 章级分段写满 + `/write` 主线视图） | v1.46 |
| J | **P1 收口**：章级编排按场推进 + 设定兜底生成 + 章级一致性巡检（体检∥质检→评审→裁决→记账） | v1.47 / v1.48 |

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

# 4) 迁移版本应为 0016（0016_ts_bigint：B28 毫秒 ts 溢出修复；A+ 批的 0015 在其下）
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

### 3.8 A4 分批交付 + 骨架落库防误删（B33 · 真 LLM）

```bash
B=http://127.0.0.1:8001/api/v1
jq_id() { node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(JSON.parse(s).id))"; }

# ① 建一本自测书
bid=$(curl -s -X POST $B/books -H 'Content-Type: application/json' \
  -d '{"title":"分批验收书","genre":"悬疑"}' | jq_id)
DIR='民国上海，一份快倒闭的小报要查出三年前沈宅灭门案的真相。'

# ② 首批只排 2 章（真 LLM，约 40s）——期望 batch={from:1,to:2,asked:2,got:2,trimmed:0}，
#    且首批必须带完整 worldview（B35：rules_text 非空）
curl -s -X POST $B/books/$bid/plan -H 'Content-Type: application/json' \
  -d "{\"direction\":\"$DIR\",\"batch_size\":2,\"batch_from\":1}" > /tmp/p1.json
node -e "const j=require('/tmp/p1.json');console.log('batch',JSON.stringify(j.batch));console.log('章目',j.plan.chapters.map(c=>c.seq+':'+c.title).join(' | '));console.log('rules_text 非空',!!(j.plan.worldview||{}).rules_text)"

# ③ 追加落库（默认 mode=append）：旧章与正文不动，章号往后接
curl -s -X POST $B/books/$bid/plan/commit -H 'Content-Type: application/json' \
  -d "{\"plan\":$(cat /tmp/p1.json | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s).plan)))"),\"mode\":\"append\"}"
# 期望：{mode:append, chapters:2, total_chapters:2, replaced_chapters:0, kept_prose_scenes:0}

# ④ 续排第 3–4 章（承接前文）再追加 → 期望 total_chapters=4、tree 里 1..4 章标题连续
curl -s -X POST $B/books/$bid/plan -H 'Content-Type: application/json' \
  -d "{\"direction\":\"$DIR\",\"batch_size\":2,\"batch_from\":3}" > /tmp/p2.json
curl -s $B/books/$bid/tree | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(JSON.parse(s).chapters.map(c=>c.order_no+'.'+c.title).join(' | ')))"

# ⑤ B33 防误删：给某场写正文后调 mode=replace → **必须 409 且正文完好**；加 allow_overwrite=true 才真替换
sid=$(curl -s $B/books/$bid/tree | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(JSON.parse(s).chapters[0].scenes[0].id))")
curl -s -X PUT $B/scenes/$sid/prose -H 'Content-Type: application/json' -d '{"text":"一句话旧稿。"}'
curl -s -o /dev/null -w "replace 未确认 http=%{http_code}\n" -X POST $B/books/$bid/plan/commit \
  -H 'Content-Type: application/json' -d '{"plan":{"chapters":[{"title":"重排章","scenes":[]}]},"mode":"replace"}'
curl -s $B/scenes/$sid/prose   # 期望仍能读到「一句话旧稿。」

# ⑥ 清理
curl -s -o /dev/null -w "delete http=%{http_code}\n" -X DELETE $B/books/$bid
```

**一键回归**：`cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_plan_batches.py -q`（8 例：分批 prompt/截断/非分批兼容 + 追加保稿 / 未确认 409 / 已确认真替换 / 空书首批写世界观 / 空骨架 400）

---

### 3.9 复杂按钮的过程与明细（v1.43 · 真 LLM）

```bash
# ① 按钮直调工具 = SSE 流（与对话同一套事件：tool_start → tick → tool_done → done）
#    实测：tool_start 立刻到 · 每 2s 一个 tick（长工具不再"点了没反应"）· tool_done 带 summary + 完整 result
curl -N -s -X POST "http://127.0.0.1:8001/api/v1/scenes/scene-85bc6bfe/agent/run/stream" \
  -H 'Content-Type: application/json' \
  -d '{"tool":"prose.review","args":{"text":"夜雨刚歇，霓虹在水洼里碎成一片红绿的油彩。"},"confirm":false}'
# 期望末两帧：
#   event: tool_done   data: {"ok":true,"summary":"体检：N 条问题（高危 h）· 口吻 v 处", "result":{...}}
#   event: done        data: {"executed":[{...}]}
# 实测（288 字真场景）：30.2s · summary「体检：8 条问题（高危 3）· 口吻 0 处」· result 含 report.overall/issues/voice_prior

# ② 顶栏按钮（含队列路径「审稿会」）：一条命令出图 + 断言（自动切责编 tab / 过程卡 / 裁决 / 明细富卡）
bash scripts/ui-meeting-probe.sh
# 期望：editorTab=true + verdict + steps(体检→润色→质检→评审) + folds(3 个明细折) + card:true + items:N
# 实测：tab=?tab=editor · stages 6 段「审稿会开始 → 体检完成（问题 10 条·口吻 1 处）→ 润色完成 → 质检完成
#        → 评审完成（77 分）→ 审稿会 · 建议质量回环（总分 77 < 78）」· items 22
# 出图：docs/mockups/p3/ink/23-editor-meeting.png（另有 22-studio.png 墨/纸两张总览）

# ③ L3 ⑧/⑧b/⑨ 已把这条链路变成永久断言（10/10）：
#   ⑧ 按钮留在顶栏（审稿会 + 精修工具 7 个）· 右栏 .ec-tool=0 · 无「质检」tab · 角标 0 不显示
#   ⑧b 审计记录可收起（点 ▾ → 列表消失、正文区拿回高度；再点展开）
#   ⑨ 顶栏点「体检」→ 自动切责编 tab + 过程卡 + 结果富卡（明细可见）
node scripts/ui-spec.mjs
```

### 3.10 写手素材与流式（v1.44 · 真 LLM）

```bash
# ① 看写手**实际**拿到什么（诊断工具，走真实装配路径 service._draft_context）
cd backend && PYTHONPATH=. ../.venv/Scripts/python.exe ../scripts/dump-writer-materials.py scene-85bc6bfe ../logs/writer_materials.md
# 期望：[3] 世界状态里能看到 [item] 金属徽章 / [term] 异常者（B48 修前恒被裁）；
#       被裁项**带原因**列出（代词脏行）；[5] 记忆块**不含**账本副本（B49）；
#       [1] 场景设定缺失项标注「（未填 · 不要自行编造…）」；
#       [10] 体量：prompt ≈ 2325 字符（修前 2456，且信息更准）

# ② 写手 token 流式（SSE）：逐 token 下发，前端边收边写
curl -N -s -X POST "http://127.0.0.1:8001/api/v1/scenes/scene-85bc6bfe/prose/draft/stream" \
  -H "Content-Type: application/json" -d "{}"
# 期望：大量 event: token（实测 161 个）→ event: done {"text":...,"chars":228}
# 实测节奏：首 token 10.8s（模型思考期），随后 0.7s 内吐完 228 字

# ③ 流式中断也要落审计（已生成部分不丢）——pytest 已覆盖
.venv/Scripts/python.exe -m pytest backend/tests/test_scene_context.py -q
# 期望：14 passed（含 select_world_states 规则 / 缺口标注 / 流式逐 token / 中断保存部分稿）
```

```bash
# ④ L3 ⑩ 已把"边收边写"变成永久断言（11/11）
node scripts/ui-spec.mjs
# 期望 ⑩：{"mid": >=1, "sawBusyLabel": true, "dirty": true, "saveLabel": "💾 保存*"}
# 注意：⑨/⑩ 在 LLM 不健康时**明确 SKIP**（不再让回退路径的"0 条问题"假通过）
```
### 3.11 队列活性（v1.45 · B51）

```bash
# ① 队列活性：worker 心跳 / 队列长度 / 僵尸数（前端横幅据此提示「没有消费者」）
curl -s http://127.0.0.1:8001/health | python -c "import json,sys; q=json.load(sys.stdin)['queue']; print(q)"
# 期望：{'alive': True, 'status': 'ok', 'heartbeat_age': <90, 'queued': 0, 'in_progress': 0, 'stuck': 0}

# ② 杀 worker → 最长 90s 后 alive=false / status=no-worker（前端横幅亮）
#    正常退出会立刻删心跳键（立即发现）；被强杀则等 TTL 过期
# ③ 重启 → 上个进程留下的 running 僵尸被**自动**标 failed，日志：
#    [worker] 启动清扫：N 个遗留 running 任务已标记 failed
bash scripts/worker-supervise.sh     # 防抖：退出即重启 + 带时间日志

# ④ L3 ⑪ 已把队列活性变成永久断言（12/12）
node scripts/ui-spec.mjs
```
### 3.12 章级直写（v1.46 · 直写链路）

```bash
# ① 章正文读写（章是最小可写单位，场是可选的细分）
curl -s "http://127.0.0.1:8001/api/v1/chapters/<chapterId>/prose"
# 期望含：prose_md / words / word_count / word_target / tone / summary / scenes

# ② 章级流式直写：按 word_target 分段写满，段间自动承接
curl -N -s -X POST "http://127.0.0.1:8001/api/v1/chapters/<chapterId>/prose/draft/stream" \
  -H "Content-Type: application/json" -d "{}"
# 实测（新书·第一章，word_target=2500）：182.8s · 6940 字符 · 6636 字 · 153 段

# ③ 保存（落 chapters.prose_md，唯一真源）
curl -s -X PUT "http://127.0.0.1:8001/api/v1/chapters/<chapterId>/prose" \
  -H "Content-Type: application/json" -d "{\"text\": \"<正文>\"}"

# ④ 保存 → 章级记账自动串（P0 闭环）：PUT 之后响应应含 bookkeeping=queued
#    实测：queued → worker 12s 完成 → 账本 +2 条（林澈=废弃车站 / 金属徽章=携带）
#    手动重跑：POST /chapters/<chapterId>/bookkeeping  body {text}

# ⑤ 前端主线视图 + L3 ⑫
node scripts/ui-spec.mjs   # ⑫ /write 打开即见整章正文 + 进度条 + 「写这一章」
```

### 3.13 概览/校对/导出口径一致（v1.46 接线 · B54）

```bash
B=book-14b4b312   # 有章级直写内容的书
# ① 概览：KPI 字数 / 时间线 / 金句 / 热力 必须一致
curl -s "http://127.0.0.1:8001/api/v1/books/$B/dashboard" | python -c "import json,sys; d=json.load(sys.stdin); print(d['kpi'], d['timeline'], d['quotes'][:1], sum(d['heat']))"
# 期望：kpi.word_count == timeline[].word_count == sum(heat) == quotes 非空
# 实测：6636 / 6636 / 6636 / 金句 3 条（作者=第一章）

# ② 校对：章字数 + has_body + done
curl -s "http://127.0.0.1:8001/api/v1/books/$B/collation" | python -c "import json,sys; d=json.load(sys.stdin); print(d['kpi'], [(c['title'],c['word_count'],c['has_body'],c['done']) for c in d['chapters']])"

# ③ 导出：全书字数与目录口径
curl -s "http://127.0.0.1:8001/api/v1/books/$B/export?fmt=md" | head -8
# 期望：全书约 6,636 字 · 共 1 章；目录「1. 第一章 零里雨夜 — 6,636 字（直写）」

# ④ 全书体检能读到章正文（scenes_checked 含章级直写单元）
curl -s "http://127.0.0.1:8001/api/v1/books/$B/health-report" | python -c "import json,sys; d=json.load(sys.stdin); print(d['voice']['scenes_checked'], d['structure']['score'])"
```

### 3.14 补设定 + 章级巡检（v1.48 · P1 收口）

```bash
B=book-14b4b312; CH=chapter-c08f2b6b

# ① 补设定（只补空字段，不覆盖作者手写）
curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/settings/generate" \
  -H "Content-Type: application/json" -d "{\"force\": false}"
# 期望：{filled: N, chapter_summary: bool, scenes: 3, scenes_generated: 3}
# 实测：filled=3（3 场 content_desc）、chapter_summary=false（已有摘要未动）、14s

# ② 章级巡检（后台队列：体检 ∥ 质检 → 评审 → 裁决 → 记账）
JOB=$(curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/inspect" | python -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "http://127.0.0.1:8001/api/v1/jobs/$JOB"
# 期望 result 含 verdict / score.total / review.high / verify.risks / bookkeeping_written
# 实测（6636 字）：134.6s · 78 分 · 裁决「有 1 条高危问题 → 建议润色后再保存」· 记账 16 条

# ③ 前端：写作页动作栏应有「🧩 补全设定」「🔍 章后巡检」，右栏出现「章级巡检」卡
node scripts/ui-spec.mjs   # ⑫ 已断言按钮存在
```

### 3.15 记忆检索（RAG · v1.49）

```bash
B=book-14b4b312; CH=chapter-c08f2b6b

# ① 重建记忆索引（后台任务；首进书或大改设定后跑一次）
JOB=$(curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/memory/reindex" | python -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "http://127.0.0.1:8001/api/v1/jobs/$JOB"
# 期望 result = {chunks>0, vectors, by_kind{chapter,character,world,foreshadow,memory}, mode:"keyword"}
# 实测：25 块 · 向量 0（未配 embedding）· by_kind{chapter:2,character:5,world:13,foreshadow:4,memory:1} · 2 阶段 ~150ms

# ② 索引统计 + 向量通道状态（为什么是纯关键词，这里能看出来）
curl -s "http://127.0.0.1:8001/api/v1/books/$B/memory/stats"
# 期望 embedding.available=false 且 reason 写明「未配置 NOVEL_EMBED_*」

# ③ 关键词检索（正斜杠/中文都要能命中）
curl -s --get --data-urlencode "q=路引 涂改" --data "top_k=3" \
  "http://127.0.0.1:8001/api/v1/books/$B/memory/search"
# 期望 hits 里 kind ∈ {chapter,character,world,foreshadow}，score 降序

# ④ 本章记忆素材（写手 prompt 与前端面板共用这一份）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/recall"
# 期望 {mode, from_search, counts{prev,characters,world,foreshadows}, chars, text}
# 实测：counts={prev:0(本书仅 1 章),characters:4,world:5,foreshadows:4} · chars=827 · from_search=3

# ⑤ 前端：写作页左栏「记忆 · 写手会读到」可展开，四类计数与明细可见
node scripts/ui-spec.mjs   # ⑬ 永久回归（模式徽标 + 折叠展开 + 明细 ≥1 条）
```

**降级验收（功能不消失，只降质量）**：

```bash
# A. 没建过索引：检索为空 → 兜底直取（最近 3 章摘要 + 全部角色/世界观/未回收伏笔）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/recall"   # mode=fallback 且 counts 仍非空

# B. 没配 embedding：mode=keyword，向量通道 available=false，检索照常工作（当前生产状态）

# C. DB 不可用：store 返回 [] → recall 全量走兜底直取；prompt 里那一段退化为「（无检索素材…）」
```

**索引保鲜（v1.49.1）**：保存章正文 / 补全设定后会自动入队重建索引，不用手动点；
保存回执里带 `reindex` 字段（`queued` / `skip`（内容没变）/ `failed`），不静默。

```bash
# 验一下「保存 → 真落库 → 索引跟上」这条链（B57 回归）
curl -s -X PUT "http://127.0.0.1:8001/api/v1/chapters/$CH/prose" \
  -H "Content-Type: application/json" -d "{\"text\": \"（粘贴一段新正文）\"}"
# 期望：{"saved": true, "words": N, "unchanged": false, "bookkeeping": "queued", "reindex": "queued"}
# 再查：curl .../chapters/$CH/prose → prose_md 应为刚保存的内容（刷新不丢）
```

### 3.16 全局脉络 + 骨架体检 + 章级制作单（v1.50 · 0-token）

```bash
B=book-14b4b312; CH=chapter-c08f2b6b

# ① 全局脉络 + 骨架体检（0-token，零 LLM）
curl -s "http://127.0.0.1:8001/api/v1/books/$B/brief"
# 期望：{score, counts{high,mid,low}, issues[], chapters[{no,label,mode,mode_label,word_target,chars,status,must_rehearse,hook}],
#        metrics{chapters,written,total_words,target_sum,modes}, foreshadows{...recovery_rate,overdue}, text}
# 实测：新书 100 分（1 章 · 已写 6636 / 目标 2500）；异能007 58 分（2 章、目标字数 0 → 报 no_target）

# ② 写章级制作单（主笔裁决：这一章走直写还是涌现）
curl -s -X PUT "http://127.0.0.1:8001/api/v1/chapters/$CH/production" \
  -H "Content-Type: application/json" \
  -d "{\"mode\":\"emerge\",\"production\":{\"must_rehearse\":[\"scene-8308b5c0\"],\"hook\":\"章末留一盏没熄的灯\"}}"
# 期望：{chapter_id, mode:"emerge", production:{...}}；再查 brief → 该章 mode_label=涌现、hook 进章表；
#      GET /chapters/$CH/prose 也带 mode/production

# ③ 清空制作单的键（合并语义下 null = 删除该键，否则改回 draft 后 must_rehearse 还挂着）
curl -s -X PUT "http://127.0.0.1:8001/api/v1/chapters/$CH/production" \
  -H "Content-Type: application/json" -d "{\"mode\":\"draft\",\"production\":{\"must_rehearse\":null,\"hook\":null}}"
# 期望：production = {}

# ④ 前端：主笔页「全局脉络（N 分）」面板；写作页页头「方式：直写/涌现/混合」
node scripts/ui-spec.mjs   # ⑭ 永久回归
```

**骨架体检能查出的 8 类缺陷（确定性规则，不是模型猜）**：无字数目标 · 无节奏声明 · mode 取值非法 ·
涌现章没指定必须推演的场 · 伏笔只埋不收（已写 ≥3 章且 closed=0）· 伏笔逾期未收 ·
角色连续 ≥3 章断档 · 角色从未出场。评分 = 100 − 高危×20 − 中等×8 − 轻微×3。

### 3.17 主笔自主调研（v1.51 · 排骨架前自查）

```bash
B=book-14b4b312

# 续批才会调研（首批没有已写内容可查）
curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/jobs/plan_batch" \
  -H "Content-Type: application/json" -d '{"direction":"延续雨夜与路引的主线","batch_size":2}'
# 拿 job_id 轮询：
curl -s "http://127.0.0.1:8001/api/v1/jobs/<job_id>"
# 期望 result 里除 plan/batch 外多一个 research：
#   [{q:"雨夜 宁秋鹜", why:"确认第一章雨夜路引的前后状态", hits:[{kind,title,text}, ...]}, ...]
# 实测（真 LLM）：5 条自主查询，每条 2~3 条命中（character/chapter/foreshadow），随后正常排出第 2–4 章

# 前端：预览卡出现「主笔查了什么 · N 条（M 条有命中）」可展开；查了没中会写「记忆里没有命中」
node scripts/shot.mjs --url="/maestro?book=$B" --js="..."   # 或人工点「✦ 排下一批」
```

**设计边界（为什么不做成"给模型一堆工具随便调"）**：不可控（可能连查 10 次跑飞）、不可观测
（作者看不出它查了啥）。现在是 **模型出检索意图 / 代码执行 / 结果可展开核对**，且
**LLM 不可用时用 brief 里的确定性线索兜底**（逾期伏笔 / 断档角色 / 上一批章），调研不会消失。

### 3.18 章级生产路径裁决（v1.52 · 0-token）

```bash
CH=chapter-c08f2b6b
# 默认（mode=draft）：可以直接写
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/production-plan"
# 期望 {mode:"draft", mode_label:"直写", next_action:"draft", can_write:true, required:[], pending:[]}

# 改成涌现（指定两场必须先推演）
S1=scene-8308b5c0; S2=scene-5128352b
curl -s -X PUT "http://127.0.0.1:8001/api/v1/chapters/$CH/production" \
  -H "Content-Type: application/json" \
  -d "{\"mode\":\"emerge\",\"production\":{\"must_rehearse\":[\"$S1\",\"$S2\"]}}"
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/production-plan"
# 期望 {next_action:"rehearse", can_write:false, required:[S1,S2], pending:[S1,S2],
#       why:"还差 2 场推演…", scenes:[{turns:0, required:true}, ...]}

# 前端（写作页）：主按钮变「▶ 先推演 2 场（含「零里废弃车站」）」并跳导演台；
# 「✍ 写这一章」降级为次要按钮（可强行直写，tooltip 说明会退化）
# 截图 docs/mockups/p3/ink/33-write-emerge.png

# 复原
curl -s -X PUT ".../chapters/$CH/production" -d '{"mode":"draft","production":{"must_rehearse":null,"hook":null}}'
```

**规则要点（都写进单测了）**：draft 不需推演 · **emerge 未指定 must_rehearse 时按"全部场"处理并显式标注** ·
hybrid 只看关键场（未指定则如实说"等同直写"）· 无场 + emerge → `manual`（先去拆场）· mode 非法按直写兜底。
**写手素材**："已推演的场"块来自场摘要/定稿正文节选/回合数，空则整块不出现（不给写手塞空标题）。

### 3.19 角色分层（v1.53 · 5 档 + 默认选角 + 补档位）

```bash
B=book-0938c02c

# ① 补档位（先预览，apply=false 不落库）
curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/characters/tiers/infer?apply=false"
# 期望 {counts:{protagonist,major,antagonist,minor,extra}, assignments:[{name,role_tier_label,why}], applied:0}

# ② 确认后落库
curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/characters/tiers/infer?apply=true"
# 期望 {applied:N, total:M}

# ③ 角色库回读应带 role_tier
curl -s "http://127.0.0.1:8001/api/v1/books/$B/characters"

# ④ 新开一个 sim：默认 cast 只含主角/反派/关键配角（上限 5），并如实说明谁只作背景
cat > /tmp/sim.json <<JSON
{"book_id": "book-0938c02c", "scene_id": "scene-85bc6bfe", "resume": false}
JSON
curl -s -X POST "http://127.0.0.1:8001/api/v1/sims" \
  -H "Content-Type: application/json" --data-binary @/tmp/sim.json
# 期望返回含 cast:{cast_names:[4 位], background_names:[1 位], notes:["背景角色（1 位，不进回合）：…"]}
```

**分层规则（单测钉死）**：`protagonist` 主角 / `major` 关键配角 / `antagonist` 反派 → **进回合**；
`minor` 次要 / `extra` 背景 → **不进回合**，只作为写手素材「在场背景角色」。
选角优先级：档位 → 本章/本场文本相关度 → 名字；超出上限（5）的可演角色**降级为背景并如实列名**。
**存量兜底**：迁移默认 `minor`（不猜谁是主角），没分档时按相关度取前 5 并提示"建议补档位"，
**绝不给空 cast**（导演台会空转）。前端：人物页名后带档位徽标、详情页「档位」下拉可改、「⚖ 补档位」预览→应用。

### 3.20 场级必然事件 beats + 回合节流（v1.54 · 0-token 判定）

```bash
# ① 场景配置里应能看到主笔写下的 beats（hard/soft + 锚点）
docker exec -i novel-ink-db psql -U postgres -d novel -tAc \
  "select id, plan_cfg_json from scenes where plan_cfg_json like '%beats%' limit 3"

# ② 起一个 sim，跑 1 回合，看节流是否生效（5 名上场角色 → 只 3 人行动）
cd backend && PYTHONPATH=. ../.venv/Scripts/python.exe - <<PY
import asyncio
from app.db.repo import Repo
from app.services.service import SimulationService
async def main():
    svc = SimulationService(Repo())
    r = await svc.start(book_id="book-14b4b312", chapter_id="chapter-c08f2b6b",
                        scene_id="scene-8308b5c0", resume=False)
    print("cast:", (r.get("cast") or {}).get("cast_names"))
    async for _ in svc.stream_step(r["sim_id"]):
        pass
    sim = await svc._load_sim(r["sim_id"])
    print("round_order:", sim.scratch.get("round_order"))     # 期望 <=3 且含主角
    print("beats_status:", sim.scratch.get("beats_status"))    # 收敛时才写
asyncio.run(main())
PY
# 实测：cast 5 人 → round_order 3 人（含主角林澈）
```

**判定规则**：只判 `hard`；锚点共现（≥2 个锚点需命中 2 个），**宁可漏报不假报**；
未达成 → 收束闸门拦下（`converged=False` + stage_prompt 追加提示），**最多再推 2 回合**，
用完则 `exhausted=True` 允许带缺陷收束并如实标记 `beats_status`。
**回合节流**：每回合最多 3 人行动（`MAX_ACTORS_PER_TURN`），主角固定在场，其余按导演主动权顺序补位；
不在名单的角色本回合不思考、不发言、不决策。

### 3.21 beats 可读可编（v1.55）

```bash
SC=scene-8308b5c0; CH=chapter-c08f2b6b

# ① 写 beats（只传 beats 也必须不影响其它字段——B58 回归）
cat > /tmp/beats.json <<'JSON'
{"beats": [{"text": "霍岸把林澈的路引对折收走", "level": "hard", "anchors": ["霍岸", "路引"]},
           {"text": "两人暂时互不揭穿", "level": "soft"}]}
JSON
curl -s -X PUT "http://127.0.0.1:8001/api/v1/scenes/$SC" \
  -H "Content-Type: application/json" --data-binary @/tmp/beats.json

# ② 章节场景列表应带解析后的 beats（且标题/舞台/目标没被清空）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/scenes"
# 期望 scenes[0].beats = [{level:"hard",text:…,anchors:[…]}, {level:"soft",…}]，title 非空

# ③ 前端：写作页左栏「本章必然事件 · N」（按场归组）；主笔页「✎ 编辑场景列表」每场一条 beats 输入框
```

**回归要点**：`PUT /scenes/{id}` 用 `exclude_unset`（不是 `exclude_none`）——
只传 beats 时绝不清空标题/舞台/目标/内容；beats 合并进 `plan_cfg_json` 时保留其它导演配置。

### 3.22 收束闸门 live 验证（v1.56）

```bash
# 用 seed 的「雨夜书房」场景（它有真的 converge_fn，才会真的收束）
# 造一个**永远不可能达成**的 hard beat，观察闸门：拦两次 → 第三次放行并如实标记
cd backend && PYTHONPATH=. ../.venv/Scripts/python.exe - <<PY
import asyncio, json, time
from app.db.repo import Repo
from app.services.service import SimulationService
async def main():
    repo, svc = Repo(), SimulationService(Repo())
    ch = (await repo.list_chapters_by_book("book-rain"))[0]
    sc = (await svc.list_scenes(ch["id"]))[0]
    await svc.update_scene(sc["id"], {"beats": [{"text": "两人穿越到平行世界",
        "level": "hard", "anchors": ["平行世界", "穿越"]}]})
    r = await svc.start(book_id="book-rain", chapter_id=ch["id"], scene_id=sc["id"], resume=False)
    for i in range(12):
        await svc.step(r["sim_id"], 1)
        sim = await svc._load_sim(r["sim_id"])
        print(sim.world.turn, sim.director.converged, sim.ended,
              sim.scratch.get("beats_retry"), (sim.scratch.get("beats_status") or {}).get("ok"))
        if sim.ended: break
    await svc.update_scene(sc["id"], {"beats": []})   # 还原
asyncio.run(main())
PY
# 实测（2026-10-21）：turn1-5 未收束；turn6 收束被拦；turn7 retry=1；turn8 retry=2；
#   turn9 converged=true & ended=true & exhausted=true，
#   beats_status={"missed":["两人穿越到平行世界"],"ok":false,"retries":2}
#   全程 9 回合 / 435s（真 LLM）
```

**这条验收为什么必须 live**：v1.54 的闸门纯函数有单测，但 `step()` 当时用的是空 `plan_cfg_json`
（B59）→ 真跑时 `beats` 恒为空、闸门永远不触发；B60 又让"真拦下"那一刻崩掉。
**只有真跑一遍才会同时暴露"接线断了"和"拦下就崩"**。

### 3.23 beats 结论可查 + 导演挑目标（v1.57）

```bash
# ① 推演一场带 hard beat 的戏（用 seed 雨夜书房场景，跑 1 回合看导演目标）
cd backend && PYTHONPATH=. ../.venv/Scripts/python.exe - <<PY
import asyncio, json
from app.db.repo import Repo
from app.services.service import SimulationService
async def main():
    repo, svc = Repo(), SimulationService(Repo())
    ch = (await repo.list_chapters_by_book("book-rain"))[0]
    sc = (await svc.list_scenes(ch["id"]))[0]
    await svc.update_scene(sc["id"], {"beats": [
        {"text": "陈家老宅的锁被撬开", "level": "hard", "anchors": ["老宅", "锁"]}]})
    r = await svc.start(book_id="book-rain", chapter_id=ch["id"], scene_id=sc["id"], resume=False)
    await svc.step(r["sim_id"], 1)
    sim = await svc._load_sim(r["sim_id"])
    print("beat_target:", sim.scratch.get("beat_target"))   # {beat_id:"b1", how:"..."}
    await repo.delete(r["sim_id"]); await svc.update_scene(sc["id"], {"beats": []})
asyncio.run(main())
PY
# 期望：beat_target = {beat_id:"b1", how:"（这回合怎么逼它发生）"}；无 LLM 时 how="按确定性策略推进"

# ② 收束后结论落到场景（作者/巡检/体检都能读）
curl -s "http://127.0.0.1:8001/api/v1/scenes/<scene_id>" | grep -o "beats_status[^,]*" | head -2
# 期望 beats_status = {achieved, missed, missed_texts, evidence, judged, ok, retries, turn, at}

# ③ 章级巡检结果里带 beats 汇总；主笔页「全局脉络」的骨架体检会报 beats_unmet（中危）
```

**语义边界**：①只报**已推演过**的场（没演的场不算"未达成"）；②`beats_status` 跟着**场景**走——
sim 只是快照，换一次推演不该丢结论；③导演选目标只认"还没发生的 hard beat id"，选错作废不污染 sim。

### 3.24 前情压缩（v1.58 · 结构化前情概要）

```bash
B=book-14b4b312; CH=chapter-c08f2b6b

# ① 触发章级前情压缩（后台；卷末会自动重算本卷概要）
JOB=$(curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/digest/generate" | python -c "import json,sys;print(json.load(sys.stdin)['job_id'])")
curl -s "http://127.0.0.1:8001/api/v1/jobs/$JOB"
# 期望 stages: 前情压缩 → 概要完成（N 事件 / M 状态迁移 / 冲突 K 条）→（卷末）重算第 1 卷
# 实测（6636 字章）：6 事件 / 6 状态迁移 / 1 条冲突；卷级 digest 2027 字节

# ② 读前情（写作页面板数据源：全书 / 本卷 / 最近两章 + 冲突）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/prior"
# 期望 {book:{arc..}, volume:{arc,characters[{knows,does_not_know,holds}]}, recent:[...],
#        conflicts:[{chapter_no,type,detail}], volume_no, volume_range, volumes, text}

# ③ 微调卷边界（主笔/作者）：默认 8 章一卷；给 volumes 则以它为准
curl -s -X PUT "http://127.0.0.1:8001/api/v1/books/$B/volumes" \
  -H "Content-Type: application/json" -d '{"volume_size": 6}'
curl -s -X PUT "http://127.0.0.1:8001/api/v1/books/$B/volumes" \
  -H "Content-Type: application/json" -d '{"volumes": [{"from":1,"to":3},{"from":4,"to":8}]}'
# 边界必须递增不重叠，否则 400

# ④ 重算卷级 / 全书级
curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/digest/generate" -d '{"scope":"volume","volume_no":0}'
curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/digest/generate" -d '{"scope":"book"}'
```

**注入预算**：全书 320 / 本卷 420 / 最近两章 400 字，总预算 1100 字；超了**按层截断**（越近越不能砍）。
**冲突只报警不改写**：`conflicts` 进 `prior` 接口 → 写作页红条提示「需你裁定」；系统不自动改历史。

### 3.25 卷实体化（v1.59 · 表 + 归属同步 + 陈旧标记）

```bash
B=book-14b4b312

# ① 卷表（迁移回填后应已有行；老书无行时读路径派生回退）
curl -s "http://127.0.0.1:8001/api/v1/books/$B/volumes"
# 期望 {volumes:[{id,no,title,range_from,range_to,arc_goal,chapters,derived,range}]}

# ② 改卷边界（写卷表 → 重同步章归属 → 旧概要标 stale，不删内容）
cat > /tmp/vc.json <<'JSON'
{"volumes": [{"from": 1, "to": 1, "title": "零里雨夜", "arc_goal": "逃出零里"}]}
JSON
curl -s -X PUT "http://127.0.0.1:8001/api/v1/books/$B/volumes" \
  -H "Content-Type: application/json" --data-binary @/tmp/vc.json
# 期望 {volumes:[…], sync:{assigned:N,unassigned:[]}, digests_marked_stale:M}

# ③ 前情接口带卷名与陈旧标记
curl -s "http://127.0.0.1:8001/api/v1/chapters/chapter-c08f2b6b/prior"
# 期望 {volume_no, volume_title, volume_digest_stale, volumes:[{no,from,to}]}
```

**稳定性三条（单测钉死）**：①章归属只有 `sync_volume_assignments` 一个写者、幂等、卷外章不猜归属；
②卷表空 → 读路径派生回退（`derived=true`）且**不写库**；③卷边界变 → 概要只标 `stale`（内容保留），重算后清除。
**连锁点**：建章/删章/改章号/骨架落库（append|replace）/改卷配置 —— 全部会重算归属。

### 3.26 分卷 UI（v1.60 · 骨架树按卷分组 + 编辑器）

```bash
B=<book_id>
# ① 卷列表（真源 book_volumes，含每卷章数）
curl -s "http://127.0.0.1:8001/api/v1/books/$B/volumes"

# ② 保存分卷（整表替换；返回 sync 与 digests_marked_stale）
cat > /tmp/vols.json <<'JSON'
{"volumes": [
  {"from": 1, "to": 2, "title": "零里雨夜", "arc_goal": "逃出零里"},
  {"from": 3, "to": 4, "title": "界墙", "arc_goal": "进第一里"}
]}
JSON
curl -s -X PUT "http://127.0.0.1:8001/api/v1/books/$B/volumes" \
  -H "Content-Type: application/json" --data-binary @/tmp/vols.json
# 期望 {volumes:[{no,title,from,to}], sync:{assigned:4,unassigned:[]}, digests_marked_stale:N}

# ③ 前端：主笔页骨架树出现卷头（第 N 卷 卷名 · 第 a–b 章 · 卷目标），章挂在卷下；
#    工具栏「分卷（N）」可编辑卷名/区间或「按每 8 章重分」后保存
node scripts/shot.mjs --url="/maestro?book=$B"
```

**边界**：卷表为空时骨架树退回平铺（不报错）；保存分卷只把旧卷概要标 `stale`（不删内容）。
**已知未接**：主笔**预览卡**里还没按卷分组（`planPreview.volumes` 已可用，UI 待做）。

### 3.27 正文版本（稿本历史 + 恢复，v1.61）

```bash
CH=chapter-c08f2b6b

# ① 保存两版不同正文（每次覆盖前旧文自动进版本链）
curl -s -X PUT "http://127.0.0.1:8001/api/v1/chapters/$CH/prose" \
  -H "Content-Type: application/json" -d '{"text":"第二版"}'
# ② 稿本历史（倒序）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/revisions"
# 期望 [{version:2,source:"author",chars:…},{version:1,…}]

# ③ 恢复（必须显式 confirm；恢复本身也追加一条 restore 版本）
curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/revisions/1/restore" \
  -H "Content-Type: application/json" -d '{"confirm":true}'
# 期望 {restored_from:1, chars:N, new_version:3}

# ④ 不带 confirm → 409（恢复会覆盖当前版，必须显式确认）
curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/revisions/1/restore" -d '{}'
# 期望 409
```

**真数据 E2E（6636 字真章）**：覆盖保存 240 字测试稿 → 版本链 [v1=6940 原文, v2=240] →
恢复 v1 → 正文写回 6940 字 + 追加 v3(restore:1) —— 历史连续，原稿无损。


### 3.28 字段级记忆索引（RAG 索引升级，v1.62）

```bash
B=book-14b4b312              # 一本有「已生成 digest」的章的书
CH=chapter-c08f2b6b          # 该章（digest_json 非空）

# ① 重建索引（digest 字段要被拆成不同 kind）
curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/memory/reindex"
# 轮询 job 后看 result.by_kind，期望含：
#   chapter_fact / chapter_knowledge / chapter_hook / foreshadow_trace
#   书里有场时还必须有 scene（v1.62 修 B61：场素材以前从没进过索引）

# ② 索引统计（前端「记忆」面板同一份口径）
curl -s "http://127.0.0.1:8001/api/v1/books/$B/memory/stats"

# ③ 召回必须带「知晓范围」段（第五段，独立渲染）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/recall"
# 期望 JSON 顶层有 knowledge: [...]；text 里依次出现
#   【记忆检索 · 前情提要】→【记忆检索 · 知晓范围】→【记忆检索 · 相关角色】
#   →【记忆检索 · 世界观 / 世界状态】→【记忆检索 · 未回收伏笔】
```

**时间闸（后文不得泄漏）**：写第 N 章时，前情只能看到第 N 章之前的素材。章级 kind 白名单在
`rag/kinds.py::CHAPTER_SCOPED_KINDS`，两条检索腿共用 `store._chapter_scope()`。
真实库回归：`pytest backend/tests/test_rag_digest.py -k future_chapter`。

**真数据 E2E（「新书」2 章）**：第 1 章已有 digest；给第 2 章写入 469 字正文 → 后台生成 digest
→ 索引从 37 涨到 46 块（chapter_fact 5 / chapter_knowledge 2 / chapter_hook 2 / foreshadow_trace 8 / scene 3）；
第 2 章 recall：prev 只有第 1 章（「第一章 零里雨夜·事实」）、knowledge 1 条、foreshadows 含
「伏笔推进 · 第 1 章 埋」、**后文泄漏检查为空**。

**UI**：写作页左栏「记忆 · 写手会读到」→ 计数行 前情 N · 知晓 N · 角色 N · 世界观 N · 伏笔 N，
展开后「知晓范围」独立成组（截图 `docs/mockups/p3/ink/41-write-recall-knowledge.png`）。
L3 ⑮ 永久回归：有 digest 的书必须出现 chapter_fact/知晓、有场必须有 scene 块、recall 必须带 knowledge 段。

### 3.29 导演台 / 正文协作的尺寸与展示（v1.63）

> 这一批由作者截图反馈驱动：**「角色配置一直有问题，会展示不全，尺寸有问题」** + 「精修工具下拉逐字竖排」。
> 三处根因都不是主题/分辨率，而是**定位与宽度写死**，所以做成 L3 永久回归（⑯⑰）。

```bash
B=book-14b4b312            # 有上场角色（霍岸/裴渡/林澈/宁秋鹜/单霁）的书
S=scene-8308b5c0

# ① 导演台左栏角色卡：必须是**真实名字 + 档位**，不能是 char-xxxx 原始 id
node scripts/shot.mjs --url="/director/$S?book=$B" \
  --js="JSON.stringify(Array.from(document.querySelectorAll('.char-name')).map(x=>x.textContent))"
# 期望 ["霍岸","裴渡","林澈","宁秋鹜","单霁"]

# ② 抽屉（配置上场角色 / 全局视角）：必须完整在视口内（修复前 x = -72 / -84）
node scripts/shot.mjs --url="/director/$S?book=$B" --js="...点全局视角后量 rail-drawer..."
# 期望 drawer.x >= 0 且 drawer.x + drawer.w <= innerWidth

# ③ 正文协作「精修工具」下拉：每项单行、菜单宽 >=140（修复前 87px → 逐字竖排）
node scripts/shot.mjs --url="/studio/$S?book=$B" --js="...打开 .s2-more 后量 .s2-more-body/每项高..."
# 期望 bodyW 152、每项高 29
```

**L3 永久回归**：`node scripts/ui-spec.mjs` → ⑯ 角色配置不被裁 + 真实名字；⑰ 下拉按内容撑开 + 每项单行 + 不越出视口。

**截图**：`docs/mockups/p3/ink/42-director-rail-characters.png`（角色栏真实名字）、
`43-director-global-drawer.png`（全局视角完整）、`44-director-cast-drawer.png`（配置抽屉完整）、
`45-studio-tools-menu.png`（墨模下拉）、`docs/mockups/p3/paper/25-studio-tools-menu.png`（纸模下拉）。

### 3.30 章级写作台（两页合并，v1.65）

> 作者拍板：**A 方案 · 合并成章级工作台**；「章正文唯一真源，场正文只是可选项、可并入」；
> 「责编按整章干活，不用切作用域 —— 它可以按需查看那些部分的正文」。

```bash
CH=chapter-c08f2b6b

# ① 章级工具（同一套过程流，事件词汇与场级一致）
curl -sN -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/agent/run/stream" \
  -H "Content-Type: application/json" -d '{"tool":"chapter.scan_tone","args":{},"confirm":false}'
# 期望 event: tool_start → tool_done → done

# ② 章级审计可回查（v1.65 迁移 0025 之前落不了：scene_id 是 NOT NULL）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/agent/notes"
# 期望 notes[].chapter_id == CH 且 scene_id == null

# ③ 并入本章：不带 confirm → 409；带 confirm → 幂等追加（已并入的跳过）
curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/merge-scenes" \
  -H "Content-Type: application/json" -d '{"scene_id":"","confirm":false}'   # 409
```

**UI（章级写作台 /write/{章}）**：左栏 = 这一章（含进度条）/ 场 · N（状态 + ⇥ 并入本章）/ 前情概要 / 记忆五段；
中栏 = 整章正文；右栏 = **本章副驾**（tab：责编 / 进度·审校，含章级审计记录）；
顶栏 = 写这一章 / 补全设定 / 章后巡检 / 稿本 / **体检整章** / **审稿会** / **精修工具▾**（润色・质检・评审・AI 味・看上下文）/ 场景精修 / 编辑 / 保存。

**L3 永久回归**：⑲ 章级工作台（右栏副驾在场 + 章级工具在顶栏 + 场块可见）；⑱ /write 可直达且侧栏指向它。
**截图**：`docs/mockups/p3/ink/46-write-chapter-copilot.png`

### 3.31 章级收口（v1.66）

```bash
cd E:/novel_desk-agent/backend && ../.venv/Scripts/python.exe -m alembic current

# 迁移可回滚性（0025：prose_notes.chapter_id + scene_id 可空）
cd E:/novel_desk-agent/backend
PY=../.venv/Scripts/python.exe
$PY -m alembic downgrade -1   # → 0024：chapter_id 列与索引消失，scene_id 回到 NOT NULL
$PY -m alembic upgrade head   # → 0025：chapter_id 回来（可空）、scene_id 可空、ix_prose_notes_chapter 重建
```

```bash
# 章级工具守卫（不许要 scene_id / 不许写场正文 / 并入必须留版本）
pytest backend/tests/test_chapter_scope.py -q
# 期望 11 passed
```

**UI 收口（L3 永久回归）**：⑳ `/studio`（无 id）必须重定向到 `/write/{当前章}`（不留空路由）；
⑱ `/write` 可直达且侧栏「章级写作」指向它；⑲ 章级工作台副驾与章级工具在场。

**旧入口清单（v1.66 已统一）**：侧栏第 3 项 → `/write/{当前章}`；stepLink 的 director/dashboard/write 下一站 →
`toWrite`；主笔页 章选中 → 「✍ 去写这一章」，场选中 → 「✍ 写这一章 / ✎ 场级精修」；设定页空态 → `/write`。

### 3.32 章级骨架遵循度审计（v1.67 · prose.audit，只读）

> 主笔要「能看到正文」，但不能「改正文」。这条链路只有报告：0-token 判定 + 模型判定；
> 写库仅一条章级审计 note，正文一字不动（有测试断言）。

```bash
CH=chapter-c08f2b6b

# 审计（0-token + LLM；LLM 不可用时也必须返回 0-token 结论，不 500）
curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/audit" | head -c 600
# 期望：{ goal:{chapter,tension_curve,volume,climax}, checks:[...], deviation:{verdict,score,items,keep}, rendered }

# 只读证据：调用前后正文完全一致（测试也这么断言）
curl -s "http://127.0.0.1:8001/api/v1/chapters/$CH/prose" | head -c 80
```

**0-token 判定码**：`empty` / `words_under` / `words_over` / `no_target` / `no_curve` / `no_goal` /
`beats_unmet`（来自各场 `plan_cfg_json.beats_status`）/ `scene_not_merged` / `protagonist_absent` / `volume_no_goal`。

**UI**：写作页右栏第三个 tab「遵循度」（结论 + 章目标/本卷目标 + 0-token 清单 + 偏离项与建议 + 保持项），
顶栏另有「🧭 遵循度」按钮。截图：`docs/mockups/p3/ink/47-write-adherence-audit.png`

**L3 永久回归**：㉑ 章级副页「骨架遵循度」可跑、有结论（0-token 必在）；**只读**由 pytest + 工具声明双重保证。

### 3.33 主笔对话直连骨架审计（v1.68）

```bash
B=book-0938c02c      # 有章节的书

# 问一句就出报告（中文数字也认：「第十二章写得好不好」）
curl -s -X POST "http://127.0.0.1:8001/api/v1/books/$B/chief/tools" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"看看第 1 章写得对不对"}]}'
# 期望：fast_path=prose.audit · executed[0].tool=prose.audit · reply 含「骨架遵循度审计完成（遵循度 N）」

# 只读证据：正文前后一致（pytest 也断言）
curl -s "http://127.0.0.1:8001/api/v1/chapters/chapter-f89891c8/prose" | head -c 60
```

**L3 永久回归**：㉒ 主笔页有「🧭 第 N 章遵循度」chips → 点一次得到含「遵循度」的回答 + `prose.audit` 执行芯片。
**截图**：`docs/mockups/p3/ink/48-maestro-audit-chat.png`

**为什么不是「让模型自己选工具」**：模型不知道内部 chapter_id；LLM 不可用时也必须给结论；行为要可测。
所以对话层做意图识别 + 快速路径，模型路径同时保留（提示词里带章清单，模型可用 chapter_no 调 `prose.audit`）。

### 3.34 方案②：建议→候选→采纳→版本 + 两级缓存（v1.69）

**缓存键（作者规格，逐条）**

```
audit   novel_ink:cache:audit:{chapter_id}:{body_hash}:{plan_hash}      TTL 24h
rewrite novel_ink:cache:rewrite:{chapter_id}:{body_hash}:{sha1(instruction|scope)[:12]}  TTL 10min
lock    <上面两个键>:lock                                            SET NX PX 30s
body_hash = sha256(全文)；plan_hash = sha256(章摘要/张力曲线/字数目标/卷目标/0-token 检查码)
```

```bash
CH=chapter-c08f2b6b

# ① 审校缓存：第一次 miss（打 LLM），第二次应 hit 且极快
curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/audit" | python -c "import sys,json;print(json.load(sys.stdin)['cache'])"
# 期望第 1 次 {"hit": false}、第 2 次 {"hit": true}（实测 11.1s → 0.03s）

# ② 候选缓存：同一 instruction 第二次命中（实测 12.1s → 0.02s）
curl -s -X POST "http://127.0.0.1:8001/api/v1/chapters/$CH/rewrite" \
  -H "Content-Type: application/json" -d '{"instruction":"合并重复查验","scope":"chapter"}'

# ③ 采纳：无 confirm → 409；有 confirm → 写库 + 旧文存版本（prev_revision）
curl -s -X POST "http://127.0.0.1:8001/api/v1/prose-notes/<note_id>/apply" \
  -H "Content-Type: application/json" -d '{"confirm":false}'      # 409
```

**缓存用例（pytest `test_rewrite_cache.py`）**：命中/不命中 · 正文变更必然失效（`body_hash` 变）·
sha256 与「长度+首尾」的区别 · single-flight 并发只跑一次 · **Redis 不可用降级直算不报错且不卡 45s**。

**安全三件套**：候选**永不自动落库**；采纳必须 `confirm`；`based_on`（候选生成时的 body_hash）与当前正文不符 → 拒绝覆盖。
采纳走 `save_chapter_prose`，因此自动获得：版本链（`prev_revision` 可回退）+ 记账 + 索引 + 前情重算。

**L3 永久回归**：㉓ 遵循度 tab →「✍ 按这条改」→ 候选卡（未落库）出现且带「✓ 采纳并保存」（L3 刻意不点采纳，避免改夹具正文）。
**截图**：`docs/mockups/p3/ink/49-write-suggestion-candidate.png`

### 3.35 方案② 完成度审计与缺陷修复（v1.70）

> 审计方法：**先写「应当通过」的回归用例 → 在 v1.69 上跑红（证明缺陷真实）→ 修复 → 转绿**。
> 7 个疑点全部实证，其中 2 个阻断级（数据损失风险）。

| # | 级别 | 缺陷 | 证据（实测） | 修复 | 回归用例 |
| --- | --- | --- | --- | --- | --- |
| B66 | 阻断 | scope=para 喂整章给模型，splice 把整章塞回一段 → 正文膨胀 | 探针：22 字 → 38 字（1.7 倍） | prompt 只喂该段 | `test_para_scope_only_sends_and_replaces_that_paragraph` |
| B67 | 阻断 | 章 >8000 字整章改写只喂尾部 → 采纳砍掉前文 | 探针：9001 → 8030 字（丢头部 971 字） | 整章 >8000 拒绝 + 候选骤短 <60% 护栏 | `test_chapter_scope_refuses_too_long_body` / `..._rejects_suspiciously_short_candidate` |
| B68 | 应修 | release_lock 无条件 DEL → 锁过期误删别人的锁 | 代码审查 + token 测试 | token + Lua compare-and-del | `test_release_lock_only_removes_own_token` |
| B69 | 应修 | 每操作新建 Redis 连接（single-flight 等待 250ms/次 → 连接风暴） | 计数探针：3 次操作 3 条连接 | 进程内共享客户端（按事件循环） | `test_redis_client_is_reused` |
| B70 | 应修 | 命中缓存仍新建 note（审计/候选）→ 审计表噪声 | 用例：2 次审计 → 2 条 note | note 创建进计算体、随结果缓存 | `test_cache_hit_does_not_duplicate_*` |
| B71 | 应修 | 并发 apply 竞态：都能过 hash 校验各写一次 | 用例：2 并发 → save 2 次 | 每 note 一把互斥锁 + 锁内重读 | `test_concurrent_apply_writes_once` |
| B72 | 应修 | 「按这条改」失败只闪 2.4s toast → 作者以为没反应 | LLM 限流实测：无候选、无提示 | 审计面板持久错误行 + L3 ㉓ 改为「候选或错误」二选一必现 | `L3 ㉓` |

**未落地项补齐（方案② 清单对照）**：审计 `scope_hint`（原 0 处，现 schema+normalize+前端类型）；
L1 章清单缓存真正接线（`_chapters_cached` + 章 CRUD 即时失效；此前 TinyTTLCache 定义了无调用方）；
候选卡范围切换（整章 / 第 N 段）。**仍属可延后**：审计建议到「段」的自动定位（需段落级映射，与 ③ 一起做）。

**收口审计（同批）**：路由 13 条全有落点（`/studio` 无 id 重定向）；`/studio/:sceneId` 为二级场精修页（有意保留）；
临时脚本已清；`WorkspaceContext.stepLink`/主笔页/设定页三处旧入口统一到 `/write`。

**验收（v1.70）**：pytest **468** · tsc 0 · build ok · L2 **25/25** · L3 **24/24**（㉓ 现同时覆盖「限流时错误持久可见」）。
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
| **写权限矩阵强制** | ✅ 已启用（v1.30）：`check_write` 接进 ToolExecutor（`scenes:final_prose`/`prose_annotations` 字段级闸门）；`agent_tasks` 由 tasks.py 发起者白名单强制；其余行 enforced=False（无工具写路径/走 UI 直调，**诚实标注非漏做**） |
| **Redis** | 未引入（触发条件：多 worker / 后台长任务 / 多人实时 / 高频进度）；纪律：只做传输·缓存·锁，不放业务事实 |
| **素材库（RAG）** | ✅ **v1.49 已落地检索层**：`mem_chunks`（7 类 kind）+ zhparser 中文关键词检索 + `memory.search` 工具 + 写手 prompt 记忆段 + 写作页「记忆」面板 + arq 重建任务；向量通道代码/表/索引已就绪，**只差 embedding key**（百炼账号欠费）。**仍缺**：rerank / 多查询改写 / 跨书素材库页面（侧栏仍是灰态占位） |
| **成书 / 导出** | ✅ v1.31 + **v1.34 收口第一刀**（字数唯一口径 · 导出加目录「章 + 字数 + 场次」）；**v1.35 补 C**：逐章校对页 `/proofread`（每章场景清单 / 字数 / 定稿状态 / 一键去改）；**仍缺**（作者已明确暂缓）：epub·docx / 分卷。历史：`export_book_md` 场景间 `* * *` 分隔 + 全书字数统计；概览页头部「导出成书」入口（有定稿章才显示）；阅读台原入口保留 |
| **世界状态浏览视图** | ✅ 已交付（v1.28 浏览 / v1.29 作者编辑）：`GET/POST/PUT/DELETE /world-states` + 设定页真列表（kind 过滤 / 前值 / 作者手改徽标 / 行内编辑与新增） |
| **A4 分批交付** | ✅ 已交付（v1.33）：每批 N 章 → 预览复核 → **增量落库**（默认 `mode=append`，旧章与正文不动）→ 确认后「✦ 继续下一批」；首批给全世界观（B35），骨架全量替换需显式确认且已有正文时被 409 拦住（B33） |
| **候选池 T1–T8** | 仍待考虑（场景快照 / 文学减法 / 主笔回溯 / 叙事选择 / 角色非理性授权 / 角色卡扩展 / 事实硬闸门 / 四类约束表） |
| **README** | 未写（作者明确"项目还没完全做好"） |

---

*本清单 v1.0；后续每批落地按同一格式追加「命令级验收」段落，并同步 Wiki §0 版本历史。*
