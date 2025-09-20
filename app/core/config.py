from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingConfig(BaseModel):
    level: str = Field(default="INFO", alias="LOG_LEVEL")


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = Field(default="development", alias="APP_ENV")
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")
    timezone: str = Field(default="America/New_York", alias="PRIMARY_TIMEZONE")

    google_project_id: str = Field(default="", alias="GOOGLE_PROJECT_ID")
    google_delegated_user: str = Field(default="", alias="GOOGLE_DELEGATED_USER")
    google_calendar_id: str = Field(default="", alias="GOOGLE_CALENDAR_ID")
    google_service_account_path: Path | None = Field(default=None, alias="GOOGLE_SERVICE_ACCOUNT_PATH")
    google_auth_method: str = Field(default="service_account", alias="GOOGLE_AUTH_METHOD")
    google_oauth_client_secrets_path: Path | None = Field(default=None, alias="GOOGLE_OAUTH_CLIENT_SECRETS_PATH")
    google_oauth_token_path: Path | None = Field(default=None, alias="GOOGLE_OAUTH_TOKEN_PATH")

    database_url: str = Field(default="sqlite:///./data/crm.db", alias="DATABASE_URL")

    retell_webhook_token: str | None = Field(default=None, alias="RETELL_WEBHOOK_TOKEN")

    logging: LoggingConfig = Field(default_factory=LoggingConfig)


@lru_cache
def get_settings() -> AppConfig:
    return AppConfig()


def get_config_value(key: str, default: Any | None = None) -> Any:
    settings = get_settings()
    return getattr(settings, key, default)
