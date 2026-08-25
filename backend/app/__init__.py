"""涌现式小说 Agent · 后端（B 方案五层架构）。

目录分层（FastAPI 社区共识）：
  schemas/  Pydantic 领域模型 + DTO
  data/     DB 会话(依赖注入) + Repository
  services/ 业务编排(SimulationService) + engine
  api/      FastAPI 组装 + routers
"""