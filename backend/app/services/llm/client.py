"""OpenAI 兼容 chat/completions 客户端（async）。

封装模型分档路由（config.model_strong / model_cheap），供角色思考层/导演调用。
v1.43：两档默认同模型（deepseek-v4.1-flash），不再按强弱分流；接口保持不变，
需要时改 .env 一行即可恢复分档。
任何失败（无 key / 网络 / JSON 解析）一律返回 None，调用方走确定性回退脚本，
绝不向引擎抛异常 —— 保证无 API Key 也能跑通「涌现 + 护栏」闭环。

异步化：httpx.AsyncClient 单例复用；应用关闭时在 lifespan 调 await client.aclose()。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# LLM 可用性度量（B32）：失败**不再静默** —— 累计次数 + 最近一次错误，由 /health 的 llm 段上报、前端横幅可见。
# 为什么必须做：模型名写错（404）/账号异常（400）时，客户端原本 except: return None，
# 全系统静默走确定性回退（角色思考/决策/成文全变写死脚本），而界面与 /health 一切正常 = 典型「假成功」。
_LLM_STATE: dict[str, Any] = {
    "calls": 0, "failures": 0, "ok": True,
    "last_error": "", "last_status": 0, "last_model": "", "last_at": 0,
}


def _note_failure(model: str, status: int, error: str) -> None:
    _LLM_STATE.update({
        "calls": _LLM_STATE["calls"] + 1,
        "failures": _LLM_STATE["failures"] + 1,
        "ok": False,
        "last_error": str(error)[:300],
        "last_status": status,
        "last_model": model,
        "last_at": int(time.time() * 1000),
    })
    logger.warning("[llm] 调用失败 model=%s status=%s：%s", model, status, str(error)[:200])


def _note_success() -> None:
    _LLM_STATE.update({"calls": _LLM_STATE["calls"] + 1, "ok": True})


def llm_status() -> dict[str, Any]:
    """LLM 健康度（/health 用）：configured=配了 key；ok=最近一次调用是否成功。"""
    return {
        "configured": bool(settings.openai_api_key),
        "base_url": settings.openai_base_url,
        "model": settings.model_cheap,
        "model_strong": settings.model_strong,
        # v1.43：是否同档（true = 不分强弱，全链路同一个模型）
        "unified": settings.model_strong == settings.model_cheap,
        **_LLM_STATE,
    }


class LLMClient:
    """极简 OpenAI 兼容客户端：只覆盖本系统需要的 /chat/completions 端点。"""

    def __init__(self, timeout: float = 120.0) -> None:
        self.base_url = settings.openai_base_url.rstrip("/")
        self.api_key = settings.openai_api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            self._client = httpx.AsyncClient(headers=headers, timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    @property
    def available(self) -> bool:
        """未配置 key（NOVEL_OPENAI_API_KEY 为空）即视为不可用。"""
        return bool(self.api_key)

    async def _chat(self, model: str, prompt: str, json_schema: Optional[Any] = None,
                    temperature: float = 0.7) -> Optional[Any]:
        """单次对话。无 schema → 返回内容字符串；有 schema → 返回解析后的 dict。

        任何异常（未配置/网络/状态码/JSON 解析）都返回 None（调用方走确定性回退），
        但**不再静默**：记 _LLM_STATE + WARNING 日志，并由 /health 的 llm 段上报（B32）。
        """
        if not self.available:
            _note_failure(model, -1, "未配置 NOVEL_OPENAI_API_KEY")
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
            resp = await self._http().post(
                f"{self.base_url}/chat/completions",
                json=payload,
            )
            if resp.status_code >= 400:
                # 404=模型名不存在；400=账号/权限；429=限流；5xx=服务端 —— 全部留痕，一眼看出「LLM 其实没在工作」
                _note_failure(model, resp.status_code, resp.text)
                return None
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            _note_failure(model, 0, f"{type(e).__name__}: {e}")
            return None

        if json_schema is not None:
            try:
                parsed = json.loads(content)
            except Exception as e:  # noqa: BLE001
                _note_failure(model, 200, f"JSON 解析失败：{e}；原文前 120 字：{str(content)[:120]}")
                return None
            _note_success()
            return parsed
        _note_success()
        return content

    async def call_strong(self, prompt: str, json_schema: Optional[Any] = None) -> Optional[Any]:
        """强模型：角色演绎（思考/决策）。"""
        return await self._chat(settings.model_strong, prompt, json_schema)

    async def call_cheap(self, prompt: str, json_schema: Optional[Any] = None) -> Optional[Any]:
        """廉模型：导演/张力评估/成文。"""
        return await self._chat(settings.model_cheap, prompt, json_schema)

    async def chat_stream(self, prompt: str, temperature: float = 0.8,
                          model: Optional[str] = None) -> AsyncIterator[str]:
        """流式纯文本生成（成文/对话/内心独白）：逐 token yield 文本增量。

        model 缺省用廉模型（导演/主笔/成文）；角色独白等需要时可显式传强模型
        （如 settings.model_strong）。不可用/失败则空流结束（调用方负责回退）。
        注意 OpenAI 兼容流式字段在 `choices[0].delta.content`。
        """
        if not self.available:
            return
        payload: dict[str, Any] = {
            "model": model or settings.model_cheap,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "stream": True,
        }
        try:
            async with self._http().stream(
                "POST", f"{self.base_url}/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                    except Exception:  # noqa: BLE001
                        continue
                    token = delta.get("content")
                    if token:
                        yield token
        except Exception:  # noqa: BLE001
            return


# 模块级单例（复用一次配置解析，供角色/导演引擎引用）
client = LLMClient()