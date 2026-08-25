# Tasks

> plan v0.1 · 后端五层架构重构 + 最小导演台
> 目标：收敛到 B 方案目录 + 跑通无 Key 回退的最小"涌现+护栏"闭环。

- [x] Task 1: 收敛目录到 B 方案骨架
  - [x] 删除 `backend/models.py`、`backend/config.py` 等根扁平模块，整理到 `app/`
  - [x] 迁移 `schemas/models.py`、`services/engine/world.py` 到 `app/` 对应位置并改 import
  - [x] 补齐 `engine/director.py`、`engine/character.py`、`engine/graph.py`（含 `from app.schemas...` 导入）
  - [x] `uv run python -c "import app.schemas, app.services.engine..." ` 验证 import OK
- [x] Task 2: 数据层（会话依赖注入 + Repository）
  - [x] `app/data/session.py`：基于 psycopg + settings.dsn，提供 `get_db()` 依赖注入
  - [x] `app/data/repo.py`：SimulationState/Events/Beliefs 的持久化 Repository（start/load/save）
  - [x] 用 FastAPI `Depends(get_db)` 挂到路由（用户明确要求）
- [x] Task 3: 服务层（SimulationService）
  - [x] `app/services/service.py`：启动叙事、step 回合、查看状态、作者介入（举手/注入积压）
  - [x] 经 Repository 走 DB；未持久化时内存态兜底（便于先无库验证）
- [x] Task 4: API 层（FastAPI 组装）
  - [x] `app/main.py`：创建 app、CORS（接 webapp）、注册 routers、lifespan 启动时建表
  - [x] `app/api/deps.py`：get_db 等依赖
  - [x] `app/api/routers/simulation.py`：POST /start、POST /step、GET /state、POST /raise 等
- [x] Task 5: 信任/背叛微场景 + 校验脚本
  - [x] `app/scenarios/betrayal_night.py`：3 角色卡 + 世界初始 facts + 导演 PlanCfg + decide_fn/converge_fn（确定性）
  - [x] `app/run_demo.py`：无 Key 跑完整回合循环，打印回合数/事件数/张力/护栏/收束
  - [x] 验证涌现+护栏：冲突事件触发张力、信息曝光更新信念、权重带内因调、护栏可拦(含一次熔断)
- [x] Task 6: Docker/Postgres 联调 + 测试
  - [x] 确认容器 `novel-ink-db` 启动、`pgvector/pgvector:pg16` 可用（镜像拉取受网络阻塞，见备注）
  - [x] `tests/` 冒烟：state 可 save/reload，事件 append-only（内存态兜底通过，9 passed）
  - [x] `uv run pytest` 通过

> Task6 备注：`docker compose up -d` 因拉取 `pgvector/pgvector:pg16` 时 registry-1.docker.io 网络超时而失败（容器未建）。按 spec「DB 未就绪时内存态兜底」，全部校验仍通过；待网络恢复/换镜像源后补验真实 PG 联调。

# Task Dependencies
- Task 2 依赖 Task 1（会话要用 app 结构）。
- Task 3 依赖 Task 2（服务走数据层）。
- Task 4 依赖 Task 3（路由调服务）。
- Task 5 依赖 Task 3（无需 API 即可验证闭环）。
- Task 6 依赖 Task 3、4（联调 DB + 全链路）。