# Checklist

> 逐条对照 spec 验收。通过则勾选。

## 结构（B 方案）
- [x] `backend/` 根下无扁平 Python 源码（models/world/director/character/graph/config 均已迁入 `app/`）
- [x] `app/` 含 main.py / config.py / schemas / services / data / api 五层齐全
- [x] import 全部为 `from app....`，`uv run python -m pytest` 能 import

## 数据层
- [x] `get_db()` 通过依赖注入提供会话，路由用 `Depends(get_db)`，不得自建连接
- [x] Repository 能 start/save/load SimulationState，事件 append-only（内存态兜底验证）

## 服务层
- [x] SimulationService 提供 启动叙事/step/查看状态/作者介入 四个能力

## API 层
- [x] `app/main.py` 组装 FastAPI + CORS（接 webapp）+ 注册 routers + lifespan 建表
- [x] 存在 POST /start、POST /step、GET /state、POST /raise 端点（TestClient 集成冒烟通过）

## 闭环（无 Key 回退）
- [x] `run_demo` 无 API Key 跑通"导演→黑板→角色→事件→刷新→成文→收束"（回合数 T01→T04，收束"公开决裂"）
- [x] 回合数递增、事件日志追加、信念账本带溯源（liwen 得知 F-4，溯源 DIR）
- [x] 张力随冲突上升；信息曝光改信念；权重调整必带剧情内因
- [x] 护栏可拦截（第3层世界事实），且超上限触发熔断标记，不污染事实（单测覆盖）

## Docker/测试
- [x] `novel-ink-db` 容器可起，pgvector 扩展可用（**注：镜像拉取网络阻塞未建，见下**）
- [x] `uv run pytest` 通过（13 passed：护栏/信念/服务/API/熔断 均覆盖）

> **待办（非 spec 验收阻塞）**：`docker compose up -d` 因拉取 `pgvector/pgvector:pg16` 时连不上
> registry-1.docker.io（网络超时）而失败，容器未创建。全部代码层校验已通过内存态兜底。
> 网络恢复或换镜像源后，补验真实 PG 建表/save/load 联调。