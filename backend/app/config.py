"""运行配置：DB（PostgreSQL+pgvector）、LLM 路由、模型分工（强/廉）。

用环境变量覆盖默认值（对齐 MVP §5.1「强模型=角色演绎，廉价模型=导演/成文/评估器」）。
默认关闭 LLM：未配 `NOVEL_OPENAI_BASE_URL` + key 时，LLM 路由自动落到**确定性回退**，
保证「涌现+护栏」闭环没有 API Key 也能端到端跑通、便于前端打通。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---- 数据（PostgreSQL + pgvector） ----
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "novel"
    db_host: str = "localhost"
    db_port: int = 5433

    # ---- LLM（OpenAI 兼容协议） ----
    openai_base_url: str = ""                # 例：https://api.openai.com/v1
    openai_api_key: str = ""                 # 例：sk-xxx
    model_strong: str = "gpt-4o-mini"        # 角色演绎（强）
    model_cheap: str = "gpt-4o-mini"         # 导演/张力评估/成文（廉）

    # ---- 涌现与护栏参数 ----
    max_turns: int = 60                      # 微场景回合上限（防死循环）
    guard_retry_max: int = 2                 # 护栏打回重演上限（熔断，§7）
    hand_raise_auto_pause: bool = False      # 举手时是否自动暂停
    scenario_name: str = "betrayal_night"    # 默认微场景

    # ---- 持久化开关：DB 未就绪时允许内存态跑通闭环 ----
    persist: bool = True

    # ---- 回退模式确定性 seed，便于"同剧本三次运行对比" ----
    seed: int = 42

    model_config = SettingsConfigDict(
        env_prefix="NOVEL_",
        # 指向项目根目录 .env（非 backend/.env），避免从 backend/ 启动时读不到
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{quote(self.db_password, safe='')}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()