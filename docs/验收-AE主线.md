
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
| **素材库（RAG）** | 侧栏灰态占位「RAG 后」；属 A1 上下文 + 跨书素材，等触发 |
| **成书 / 导出** | ✅ v1.31 + **v1.34 收口第一刀**（字数唯一口径 · 导出加目录「章 + 字数 + 场次」）；**v1.35 补 C**：逐章校对页 `/proofread`（每章场景清单 / 字数 / 定稿状态 / 一键去改）；**仍缺**（作者已明确暂缓）：epub·docx / 分卷。历史：`export_book_md` 场景间 `* * *` 分隔 + 全书字数统计；概览页头部「导出成书」入口（有定稿章才显示）；阅读台原入口保留 |
| **世界状态浏览视图** | ✅ 已交付（v1.28 浏览 / v1.29 作者编辑）：`GET/POST/PUT/DELETE /world-states` + 设定页真列表（kind 过滤 / 前值 / 作者手改徽标 / 行内编辑与新增） |
| **A4 分批交付** | ✅ 已交付（v1.33）：每批 N 章 → 预览复核 → **增量落库**（默认 `mode=append`，旧章与正文不动）→ 确认后「✦ 继续下一批」；首批给全世界观（B35），骨架全量替换需显式确认且已有正文时被 409 拦住（B33） |
| **候选池 T1–T8** | 仍待考虑（场景快照 / 文学减法 / 主笔回溯 / 叙事选择 / 角色非理性授权 / 角色卡扩展 / 事实硬闸门 / 四类约束表） |
| **README** | 未写（作者明确"项目还没完全做好"） |

---

*本清单 v1.0；后续每批落地按同一格式追加「命令级验收」段落，并同步 Wiki §0 版本历史。*
