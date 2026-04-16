from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Telegram
    telegram_bot_token: SecretStr
    telegram_chat_id: int

    # Hevy
    hevy_api_key: SecretStr
    hevy_webhook_secret: SecretStr

    # OpenRouter — PydanticAI also reads OPENROUTER_API_KEY automatically
    openrouter_api_key: SecretStr
    openrouter_model: str = "anthropic/claude-sonnet-4-5"

    # Paths
    kb_dir: Path = Path("muscle_ladder")
    coach_log_path: Path = Path("coach_log.md")

    # Server
    host: str = "0.0.0.0"
    port: int = 8080


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]  # fields come from env/.env
    return _settings
