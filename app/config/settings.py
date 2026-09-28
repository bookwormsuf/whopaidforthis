"""Application settings, loaded from environment variables (or a local .env file)."""

from __future__ import annotations

import sys
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Required secrets and config. Values are never logged or printed."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    telegram_bot_token: SecretStr
    google_service_account_json: SecretStr = Field(
        description=(
            "The Google service account key as JSON text on one line, not a path to a file. "
            "Not parsed yet; Sprint 1 does that."
        )
    )
    anthropic_api_key: SecretStr
    log_level: LogLevel = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


def load_settings() -> Settings:
    """Load Settings from the environment, exiting with code 1 on any missing or invalid value.

    Prints one line per problem to stderr:
        Missing required environment variable: TELEGRAM_BOT_TOKEN
        Invalid value for environment variable: LOG_LEVEL (<pydantic message>)
    Never prints the offending input value.
    """
    try:
        return Settings()
    except ValidationError as exc:
        for error in exc.errors():
            field_name = str(error["loc"][0]) if error["loc"] else "unknown"
            env_name = field_name.upper()
            if error["type"] == "missing":
                print(f"Missing required environment variable: {env_name}", file=sys.stderr)
            else:
                print(
                    f"Invalid value for environment variable: {env_name} ({error['msg']})",
                    file=sys.stderr,
                )
        raise SystemExit(1) from exc
