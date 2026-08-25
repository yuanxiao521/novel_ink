"""FastAPI 应用组装（Api 层入口）。

- CORS：放行 webapp 开发服务器（供端到端打通）
- lifespan：启动时初始化 DB schema（Repo.init_schema）
- 路由：simulation
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_repo
from app.api.routers import simulation

# webapp 常用开发端口（静态 http.server / vite 等），可扩
ALLOWED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000",
                   "http://localhost:5173", "http://127.0.0.1:5173"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动时尝试建表；DB 未就绪则忽略（走内存态兜底）
    try:
        get_repo().init_schema()
    except Exception:
        pass
    yield


app = FastAPI(title="涌现式小说 Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulation.router)


@app.get("/health")
def health():
    return {"status": "ok"}