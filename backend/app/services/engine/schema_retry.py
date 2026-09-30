"""结构化输出纠错循环（主笔升级 · 阶段③）。

对 LLM 结构化产出统一做：schema 校验 → 失败重试（带错误反馈重生成）→ 仍失败抛 ValueError（不落库）。
validator 是纯函数（0 token），由调用方按各自 JSON schema 提供；
重试次数取 settings.guard_retry_max（=导演护栏熔断上限，项目既有约定）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from app.config import settings

logger = logging.getLogger(__name__)


async def validate_and_retry(
    llm_client: Any,
    prompt: str,
    json_schema: Any,
    validator: Callable[[Any], str | None],
    retries: int | None = None,
) -> Any:
    """带诊断重试的结构化 LLM 调用。

    validator(raw) → None 表示通过；否则返回错误描述字符串（拼回 prompt 让 LLM 修正重生成）。
    全部尝试（首次 + retries 次重试）失败 → 抛 ValueError（调用方转 422/503，**不落库**）。
    """
    retries = settings.guard_retry_max if retries is None else retries
    last_error = "结构非法（LLM 未返回）"
    attempt = 0
    while attempt <= retries:
        raw = await llm_client.call_cheap(prompt, json_schema)
        if raw is None:
            last_error = "模型无响应（返回 None）"
        else:
            err = validator(raw)
            if err is None:
                return raw
            last_error = err
        if attempt < retries:
            logger.info("[schema-retry] 输出校验失败（%s），第 %s 次重试 …", last_error[:60], attempt + 1)
            prompt = (
                f"{prompt}\n\n【纠错 {attempt + 1}/{retries}】你上一次的输出未通过校验：{last_error}\n"
                f"请依据原 JSON schema 修正后，只返回规范的 JSON（不要解释）。"
            )
        attempt += 1
    raise ValueError(f"主笔输出经 {retries + 1} 次尝试仍校验失败：{last_error}")