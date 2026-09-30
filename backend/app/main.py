"""FastAPI 应用组装（Api 层入口）。

- CORS：放行 webapp 开发服务器（供端到端打通）
- lifespan：启动时初始化 DB schema（Repo.init_schema）
- 路由：simulation
- 异常：全局兜底 → 领域异常统一转 HTTPException；未预期异常记日志并回 500
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import simulation
from app.errors import AppError
from app.services.llm.client import client as llm_client

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parent / "logs"  # backend/logs


def _setup_logging() -> None:
    """日志兜底：终端 + 文件双写，解决 uvicorn 接管 root 后业务日志不可见/不滚动问题。

    - 终端：basicConfig 只在 root 无 handler 时生效（独立运行脚本命中）；
      uvicorn 接管后 root 已有 handler，此时靠下面的 setLevel 让 INFO 可见。
    - 文件：RotatingFileHandler 挂 root（5MB×3 轮转，UTF-8），无论终端怎么缓冲，
      backend/logs/app.log 一定能看到 [think-stream]/[decide]/[director-*] 全文。
    """
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%H:%M:%S")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("app").setLevel(logging.INFO)

    os.makedirs(_LOG_DIR, exist_ok=True)
    fh = RotatingFileHandler(_LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024,
                             backupCount=3, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logging.getLogger().addHandler(fh)


_setup_logging()

# webapp 常用开发端口（静态 http.server / vite 等），可扩
ALLOWED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000",
                   "http://localhost:5173", "http://127.0.0.1:5173"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 迁移改为手动执行：`uv run alembic upgrade head`（backend/ 下，见 Wiki）
    # 启动不再自动跑迁移，避免每次启动额外叩 DB；漏迁会导致缺列报错，启动日志会提示。
    import logging

    logging.getLogger("app").info("启动提醒：数据库迁移请手动执行 `uv run alembic upgrade head`")
    try:
        yield
    finally:
        # 关闭 LLM async client（释放 httpx 连接池）
        await llm_client.aclose()


app = FastAPI(title="涌现式小说 Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulation.router)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """领域异常统一兜底：AppError → {code, detail, status_code}。"""
    body = exc.to_http()
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """真正没接住的异常：记全量堆栈，对外只回 500 兜底文案（不泄漏内部细节）。"""
    logger.exception("未兜底异常 path=%s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"code": "InternalError", "detail": "服务内部错误，请查看后端日志", "status_code": 500},
    )


@app.get("/health")
def health():
    return {"status": "ok"}