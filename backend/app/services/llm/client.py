"""OpenAI 兼容 chat/completions 客户端（Task 2）。

封装强/廉两层模型路由（config.model_strong / model_cheap），供角色思考层调用。
任何失败（无 key / 网络 / JSON 解析）一律返回 None，调用方走确定性回退脚本，
绝不向引擎抛异常 —— 保证无 API Key 也能跑通「涌现 + 护栏」闭环。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from app.config import settings


class LLMClient:
    """极简 OpenAI 兼容客户端：只覆盖本系统需要的 /chat/completions 端点。"""

    def __init__(self, timeout: float = 60.0) -> None:
        self.base_url = settings.openai_base_url.rstrip("/")
        self.api_key = settings.openai_api_key
        self.timeout = timeout

    @property
    def available(self) -> bool:
        """未配置 key（NOVEL_OPENAI_API_KEY 为空）即视为不可用。"""
        return bool(self.api_key)

    def _chat(self, model: str, prompt: str, json_schema: Optional[Any] = None,
              temperature: float = 0.7) -> Optional[Any]:
        """单次对话。无 schema → 返回内容字符串；有 schema → 返回解析后的 dict。

        任何异常（未配置/网络/状态码/JSON 解析）都静默返回 None。
        """
        if not self.available:
            return None

        messages = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_schema is not None:
            schema_txt = (
                json_schema
                if isinstance(json_schema, str)
                else json.dumps(json_schema, ensure_ascii=False)
            )
            # 把 schema 作为约束拼进 prompt，并要求仅返回 JSON，兼容面最广
            messages[0]["content"] = (
                f"{prompt}\n\n请只返回符合以下 JSON schema 的 JSON（不要多余解释）：\n{schema_txt}"
            )
            payload["response_format"] = {"type": "json_object"}

        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception:
            return None

        if json_schema is not None:
            try:
                return json.loads(content)
            except Exception:
                return None
        return content

    def call_strong(self, prompt: str, json_schema: Optional[Any] = None) -> Optional[Any]:
        """强模型：角色演绎（思考/决策）。"""
        return self._chat(settings.model_strong, prompt, json_schema)

    def call_cheap(self, prompt: str, json_schema: Optional[Any] = None) -> Optional[Any]:
        """廉模型：导演/张力评估/成文。"""
        return self._chat(settings.model_cheap, prompt, json_schema)


# 模块级单例（复用一次配置解析，供角色/导演引擎引用）
client = LLMClient()