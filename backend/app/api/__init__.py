"""Api 层：FastAPI 组装 + 依赖注入 + 路由。"""
from app.api.deps import get_repo, get_service
from app.api.routers import simulation

__all__ = ["get_repo", "get_service", "simulation"]