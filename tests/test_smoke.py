"""Sprint 0 smoke tests: config, logging, and the bare Application build."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from telegram import Update

from app.bot.app import _error_handler, build_application
from app.bot.deps import Deps
from app.bot.handlers.basic import HELP_TEXT, help_command
from app.config.settings import Settings, load_settings
from app.logging_setup import JsonFormatter
from app.services.currencies import StaticCurrencyCatalog
from app.services.ledger_store import (
    LEDGER_UNAVAILABLE_TEXT,
    InMemoryLedgerStore,
    LedgerUnavailable,
)

REQUIRED_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "ANTHROPIC_API_KEY",
    "WHOPAID_CONTROL_SHEET_ID",
)


def _fake_deps() -> Deps:
    return Deps(
        ledger=InMemoryLedgerStore(),
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD"})),
        service_account_email="whopaid-bot@example.iam.gserviceaccount.com",
        bot_version="test",
    )


def _clear_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_settings_raises_on_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_required_env(monkeypatch)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    missing_fields = {str(error["loc"][0]) for error in exc_info.value.errors()}
    assert "telegram_bot_token" in missing_fields


def test_load_settings_exits_1_and_names_missing_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _clear_required_env(monkeypatch)
    monkeypatch.chdir(tmp_path)  # avoid picking up a real repo .env
    with pytest.raises(SystemExit) as exc_info:
        load_settings()
    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN" in stderr


def test_load_settings_all_blank_secrets_exit_1_and_name_all_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("WHOPAID_CONTROL_SHEET_ID", "")
    with pytest.raises(SystemExit) as exc_info:
        load_settings()
    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    for name in REQUIRED_ENV_VARS:
        assert f"Missing required environment variable: {name}" in stderr


def test_log_level_lower_case_is_upper_cased(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("WHOPAID_CONTROL_SHEET_ID", "fake-control-sheet-id")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    settings = load_settings()
    assert settings.log_level == "DEBUG"


def test_log_level_invalid_exits_1_with_invalid_value_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("WHOPAID_CONTROL_SHEET_ID", "fake-control-sheet-id")
    monkeypatch.setenv("LOG_LEVEL", "info2")
    with pytest.raises(SystemExit) as exc_info:
        load_settings()
    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "Invalid value for environment variable: LOG_LEVEL" in stderr


def test_load_settings_missing_control_sheet_id_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.delenv("WHOPAID_CONTROL_SHEET_ID", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        load_settings()
    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "Missing required environment variable: WHOPAID_CONTROL_SHEET_ID" in stderr


def test_load_settings_invalid_service_account_json_exits_1_with_exact_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{not valid json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("WHOPAID_CONTROL_SHEET_ID", "fake-control-sheet-id")
    with pytest.raises(SystemExit) as exc_info:
        load_settings()
    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert (
        "Invalid value for environment variable: GOOGLE_SERVICE_ACCOUNT_JSON "
        "(not valid JSON)" in stderr
    )
    assert "{not valid json" not in stderr


def test_settings_repr_does_not_leak_secret_values() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="123456:fake-token",
        google_service_account_json="{}",
        anthropic_api_key="fake-key",
        whopaid_control_sheet_id="fake-control-sheet-id",
    )
    assert "123456:fake-token" not in repr(settings)


def test_build_application_registers_start_and_help_commands() -> None:
    application = build_application("123456:fake-token", _fake_deps())
    handlers = application.handlers[0]
    commands: set[str] = set()
    for handler in handlers:
        commands |= getattr(handler, "commands", set())
    assert {"start", "help", "join", "settrip", "cancel"} <= commands


def test_json_formatter_output_parses_with_expected_keys() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    parsed = json.loads(formatter.format(record))
    assert set(parsed.keys()) >= {"ts", "level", "logger", "msg"}
    assert parsed["msg"] == "hello"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "app.test"


def test_json_formatter_redacts_secret_from_message_and_exception() -> None:
    secret = "123456:fake"
    formatter = JsonFormatter(redact=[secret])

    try:
        raise ValueError(f"failed using token {secret}")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="app.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=f"boom with {secret}",
        args=(),
        exc_info=exc_info,
    )
    line = formatter.format(record)
    assert secret not in line
    assert "[REDACTED]" in line


async def test_help_command_replies_with_html_and_mentions_balances() -> None:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await help_command(update, context)

    update.message.reply_text.assert_awaited_once_with(HELP_TEXT, parse_mode="HTML")
    assert "/balances" in HELP_TEXT


def test_build_application_registers_error_handler() -> None:
    application = build_application("123456:fake-token", _fake_deps())
    assert len(application.error_handlers) == 1


async def test_error_handler_replies_one_liner_for_ledger_unavailable() -> None:
    update = MagicMock(spec=Update)
    update.update_id = 123
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()
    context.error = LedgerUnavailable("boom")

    await _error_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML"
    )


async def test_error_handler_stays_silent_for_other_errors() -> None:
    update = MagicMock(spec=Update)
    update.update_id = 123
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()
    context.error = ValueError("some other bug")

    await _error_handler(update, context)

    update.effective_message.reply_text.assert_not_awaited()


async def test_error_handler_handles_missing_update_gracefully() -> None:
    context = MagicMock()
    context.error = LedgerUnavailable("boom")

    await _error_handler(None, context)  # must not raise
