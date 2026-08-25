"""LLM 服务层：OpenAI 兼容客户端与统一回退。"""
from app.services.llm.client import LLMClient, client

__all__ = ["LLMClient", "client"]