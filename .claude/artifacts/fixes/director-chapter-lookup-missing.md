# Bug: 导演台后端未对齐——GET /chapters/{id} 缺失致 scene→chapter→book 反查失效

> Status: FIXED
> Mode: 默认（mid-depth）
> Severity: functional（影响功能：从导演台无 book_id 上下文时无法反查书树/标题）
> Author: 主笔 Agent（lvco 项目）
> Last updated: 2026-08-26

## Symptom

前端导演台 [DirectorPage.tsx](file:///e:/novel_desk-agent/frontend/src/pages/DirectorPage.tsx#L34-L54) 在进入导演台时，若路由 state **未携带 book_id**，会走 `scene → chapter → book` 反查链路：`GET /scenes/{id}` → `GET /chapters/{chapter_id}` → `fetchBookTree(book_id)`。后端无 `GET /chapters/{id}` 路由（只暴露了子资源 `GET /chapters/{id}/scenes`），反查在第 2 步就断掉 → 书标题/书树丢失。

## Expected

`GET /api/v1/chapters/{chapter_id}` 返回章节详情（含 `book_id`），导演台据此反查书树。

## Reproduction

- 步骤：内存态 API 测试 `建书 → 建章 → GET /api/v1/chapters/{cid}`
- 测试位置：`backend/tests/test_api.py:93`（`test_chapter_detail_lookup`）
- 复现稳定性：3/3 reliably fails（stash fix 后重新 RED，见 V-2）

## Hypotheses & diagnosis

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H1 | 后端无 `GET /chapters/{id}` 路由，405 | confirmed（root cause） | failing test 初始 405 Method Not Allowed |
| H2 | 路由存在但 service/repo 缺转发 | eliminated | 加路由后发现 404"未找到章节" → 指向 repo 内存态 |
| H3 | repo.get_chapter 内存态缺 `_mem` 分支 | confirmed（第二层根因） | 加内存态分支后 200 GREEN |

## Root cause

两层根因叠加：
1. **服务层缺暴露**：repo.get_chapter 早已存在，但 service 层无转发、路由无 `GET /chapters/{chapter_id}` 端点（对称的 `/scenes/{id}` 有，章节本体缺失）。
2. **内存态盲区**：repo.get_chapter 走 `_query_or_mem(_q, None)`，在 `use_db=False` 时直接返回 fallback None，未查 `_mem`——这是与 `get_book`/`get_inspiration` 同类的既有 Pattern 缺陷（内存态 repo 方法漏 `_mem` 分支）。

## Fix

- 改动文件：
  - `backend/app/services/service.py:66-68` — 新增 `get_chapter` 转发
  - `backend/app/api/routers/simulation.py:133-139` — 新增 `GET /chapters/{chapter_id}` 路由
  - `backend/app/db/repo.py:238-240` — `get_chapter` 加内存态 `_mem.get` 分支
- 一句话：补上章节详情端点的 service+router 全链路，并修复 repo 内存态缺失的分支（与 get_book/get_inspiration 对齐）。

## Verification

- V-1: `pytest -q tests/test_api.py::test_chapter_detail_lookup` → GREEN ✓（1 passed）
- V-2: `git stash push <3 个 fix 文件>` → 同测试 **RED**（405，test 真捕获 bug）✓ → `git stash pop` 恢复
- V-3: `pytest -q tests/test_api.py tests/test_inspiration.py` → **10 passed** ✓（无回归）

## Regression test

- 路径：`backend/tests/test_api.py:93`
- 名称：`test_chapter_detail_lookup`
- 断言：`GET /api/v1/chapters/{id}` 返回 200，body 含 `id/book_id/title`

## Pattern analysis

本次 root cause 分为两个 Pattern：

| 搜索方式 | 命中数 | 是否同类隐患 |
|---|---|---|
| repo 方法含 `_query_or_mem(_q, None)` 且无内存态分支（缺 `if not self.use_db: return self._mem.get(...)`） | 需核查 | 是，get_book/get_inspiration 已修，疑似还有其他 get_* 同类 |
| `@router.get("/chapters/{id}")` 缺失（仅有 `/chapters/{id}/scenes` 子资源） | — | 仅章单体反查类使用方缺失 |

## Open questions / Follow-ups

- 核查其余 repo `get_*` 方法（`get_scene`/`get_character` 等）是否也有内存态 `_mem` 盲区——当前 `_query_or_mem` 在 `use_db=False` 无条件返回 fallback，属系统级设计点，建议在下次 dev-plan 统一加固（本次按外科手术只修 get_chapter）。

## RCA

- **何时引入**：S0/S1 数据地基阶段（repo 层初建时 `_query_or_mem` fallback 语义未覆盖单对象内存态查询；`/chapters/{id}` 端点从未创建）。
- **为什么没被发现**：导演台常规进入路径带 book_id（从书架/规划页带 state 进入），反查分支只在**直接 url 进入导演台**时触发；既有 test 无此断言。
- **预防措施**：新增 `test_chapter_detail_lookup` 回归测试（已入仓）；后续 dev-plan 统一内存态 `_query_or_mem` 语义。