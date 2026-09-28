"""Entry point: python -m app."""

from __future__ import annotations

from app.bot.app import run
from app.config.settings import load_settings
from app.logging_setup import configure_logging


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, redact=[settings.telegram_bot_token.get_secret_value()])
    run(settings)


if __name__ == "__main__":
    main()
