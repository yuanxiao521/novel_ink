# 后端五层架构重构 + 最小导演台 Spec

## Why
MVP 后端当前是"半迁移"状态：`backend/` 下既有扁平模块（`models.py`/`world.py`/`director.py`/`character.py`/`graph.py`/`config.py`），又已开始启用 B 方案骨架（`schemas/`、`services/engine/`），两套并存、import 路径混乱。需收敛到 **B 方案（backend/app 嵌套）+ 五层架构**，让目录稳定、可测试、可演进到 M4/M5，并在此地基上把"导演调度 + 世界（黑板）"最小闭环跑通。

## What Changes
- **迁移扁平模块到分层结构**，删除 `backend/` 根下的扁平源码文件（`models.py`/`world.py`/`director.py`/`character.py`/`graph.py`/`config.py`）。
- 确立 B 方案目录（FastAPI 社区共识、官方模板范式）：
  ```
  backend/
  ├── app/
  │   ├── __init__.py
  │   ├── main.py          # FastAPI 组装
  │   ├── config.py        # Settings（Postgres/LLM/剧情参数）
  │   ├── deps.py          # 依赖注入（DB 会话）
  │   ├── schemas/         # Pydantic 领域模型 + DTO
  │   ├── services/        # 业务编排
  │   │   ├── service.py   # SimulationService（回回合循环/持久化）
  │   │   └── engine/      # world / director / character / graph
  │   ├── data/            # DB 会话 + Repository（数据层，依赖注入）
  │   └── api/             # FastAPI 路由层
  │       ├── deps.py
  │       └── routers/     # simulation / intervene
  └── tests/               # 测试（pytest）
  ```
- **五层职责划分**（严格对齐）：
  | 层 | 目录 | 职责 |
  |---|---|---|
  | Schema | `app/schemas/` | Pydantic 领域模型 + 请求/响应 DTO |
  | 数据层 | `app/data/` | DB 会话（依赖注入）+ Repository 持久化 |
  | 服务层 | `app/services/` | 业务编排（SimulationService）+ 引擎 |
  | Route | `app/api/routers/` | 端点定义 |
  | Api | `app/api/` | FastAPI 组装 + deps 注入 |
- **数据库会话用依赖注入**（用户明确要求）：`get_db()` 通过 FastAPI `Depends` 提供，路由不直接握手。
- 移入引擎时修正相对导入（`from app.schemas...`），保证 `import OK`。

## Impact
- Affected specs: 无既有 spec（本 change 是首个）。
- Affected code:
  - 删除：`backend/models.py`, `backend/world.py`, `backend/director.py`, `backend/character.py`, `backend/graph.py`, `backend/config.py`
  - 迁移：`backend/schemas/models.py` → `app/schemas/models.py`；`backend/services/engine/world.py` → `app/services/engine/world.py`；补齐 `director.py`/`character.py`/`graph.py`
  - 新建：`app/main.py`, `app/config.py`, `app/deps.py`, `app/services/service.py`, `app/data/`, `app/api/`
  - 保留：`docker-compose.yml`, `.env`, `pyproject.toml`, `webapp/`

## ADDED Requirements

### Requirement: 统一的 B 方案目录结构
系统 SHALL 将后端源码统一收进 `backend/app/`，按五层（schemas/data/services/api）组织，`backend/` 根下不再存在扁平源码模块。

#### Scenario: 目录收敛
- **WHEN** 开发者在 `backend/` 根做列表
- **THEN** 仅见 `app/`、`tests/`、`pyproject.toml` 等，无扁平 Python 源码散落根目录。

### Requirement: 数据库会话依赖注入
数据访问 SHALL 通过 `get_db()` 依赖注入提供会话，路由/服务不得自行创建连接。

#### Scenario: 注入会话
- **WHEN** 路由处理器需要 DB
- **THEN** 通过 `Depends(get_db)` 取得会话，会话由 FastAPI 依赖生命周期管理（请求内开启/关闭）。

### Requirement: 最小导演台可运行闭环（无 LLM Key 回退）
系统 SHALL 在未配置 API Key 时，用确定性回退完成"导演→黑板→角色→事件(护栏+熔断)→刷新→成文→收束"回合循环。

#### Scenario: 跑通涌现+护栏
- **WHEN** 执行 `uv run python -m app.run_demo`（或等价）
- **THEN** 回合数递增至收束或上限，事件日志追加、信念账本记录溯源、张力上升、护栏可拦截并熔断，全程无 LLM Key 不报错。

## MODIFIED Requirements
（本 change 无既有需求被修改；骨架文件仅为迁移保真。）

## REMOVED Requirements
### Requirement: backend 根扁平源码模块
**Reason**: 与 B 方案冲突，import 路径混淆，无法测试隔离。
**Migration**: 迁移至 `app/` 对应分层，逻辑不删，仅挪位置 + 改 import。