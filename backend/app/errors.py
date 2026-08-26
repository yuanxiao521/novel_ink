"""统一领域异常（兜底报错规范）。

设计原则：
  - 服务/存储层只抛**领域异常**（本模块），不直接抛 KeyError/ValueError 这类
    裸异常 —— 网上层（API 路由）无法区分语义，只能猜 404/400。
  - 每个领域异常携带 `status_code` 与面向用户的中文 `detail`，
    FastAPI 全局 handler 统一转 HTTPException，省去路由逐个 try/except。
  - 引擎内部不可恢复的意外（LLM 网络抖动等）由调用方兜底回退，一般不走异常；
    只有「外部请求无法满足」才抛这里定义的异常。
"""
from __future__ import annotations

from typing import Optional


class AppError(Exception):
    """领域异常基类。子类需给出 status_code / detail（可带兜底默认值）。"""

    status_code: int = 500
    detail: str = "服务异常"

    def __init__(self, detail: Optional[str] = None, *, code: Optional[int] = None) -> None:
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)
        if code is not None:
            self.__class__.status_code = code

    def to_http(self) -> dict:
        return {"status_code": self.status_code, "detail": self.detail, "code": self.__class__.__name__}


class NotFoundError(AppError):
    """资源不存在（映射 HTTP 404）。"""

    status_code = 404
    detail = "资源不存在"


class SimNotFoundError(NotFoundError):
    """模拟（sim）不存在或已被清理。"""

    detail = "未找到模拟"


class SceneNotFoundError(NotFoundError):
    """场景不存在。"""

    detail = "未找到场景"


class BookNotFoundError(NotFoundError):
    """书籍不存在。"""

    detail = "未找到书籍"


class ChapterNotFoundError(NotFoundError):
    """章节不存在。"""

    detail = "未找到章节"


class InvalidActionError(AppError):
    """请求动作非法/参数不满足约束（映射 HTTP 400）。"""

    status_code = 400
    detail = "请求参数不合法"


class DirectorChatUnavailableError(AppError):
    """导演对话暂不可用：LLM 未配置 / 输出为空 / 对话中发生可恢复错误。"""

    status_code = 503
    detail = "导演暂时无法回应（模型未接入或响应异常）"


class RepoError(AppError):
    """存储层异常（DB 挂掉等）。调用方捕获后可走内存兜底。"""

    status_code = 500
    detail = "存储层异常"