"""全局配置：读取 .env，提供带默认值的强类型设置。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 服务 ----
    host: str = "0.0.0.0"
    port: int = 8848

    # ---- 接入 ----
    ingest: Literal["mock", "douyin", "douyin_direct", "bridge"] = "bridge"
    demo: bool = False
    douyin_room: str = ""
    douyin_cookie: str = ""
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 5001

    # ---- LLM ----
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.85
    llm_max_tokens: int = 220

    persona_name: str = "大肥鱼"
    persona_style: str = "嘴贫、爱吐槽、热心、偶尔自恋，喜欢用\"鱼\"相关梗"

    # ---- TTS ----
    tts_engine: Literal["edge", "none"] = "edge"
    tts_voice: str = "zh-CN-XiaoyiNeural"
    tts_rate: str = "+8%"
    tts_volume: str = "+0%"

    # ---- 节奏 ----
    min_speak_gap: float = 1.2
    idle_chat_seconds: float = 45.0
    announce_enter: bool = True
    announce_like: bool = False
    enter_merge_window: float = 6.0

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
