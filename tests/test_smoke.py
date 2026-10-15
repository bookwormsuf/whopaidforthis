"""Sprint 0 smoke tests: config, logging, and the bare Application build."""

from __future__ import annotations

import json
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from telegram import Update

from app.bot.app import (
    _KNOWN_COMMANDS,
    SOMETHING_WENT_WRONG_TEXT,
    STOP_SIGNALS,
    _error_handler,
    _unbound_gate,
    build_application,
)
from app.bot.deps import Deps
from app.bot.handlers.balances import balances_command
from app.bot.handlers.basic import HELP_TEXT, help_command
from app.bot.handlers.join import CLOSED_TRIP_TEXT, UNBOUND_TEXT
from app.config.settings import Settings, load_settings
from app.logging_setup import JsonFormatter
from app.models.trip import Trip
from app.services.ledger_store import (
    LEDGER_UNAVAILABLE_TEXT,
    InMemoryLedgerStore,
    LedgerUnavailable,
)
from tests.helpers import make_deps

REQUIRED_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
)


def _fake_deps() -> Deps:
    return make_deps(InMemoryLedgerStore())


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
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
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
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    settings = load_settings()
    assert settings.log_level == "DEBUG"


def test_log_level_invalid_exits_1_with_invalid_value_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("LOG_LEVEL", "info2")
    with pytest.raises(SystemExit) as exc_info:
        load_settings()
    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "Invalid value for environment variable: LOG_LEVEL" in stderr


def test_load_settings_missing_database_url_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        load_settings()
    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "Missing required environment variable: DATABASE_URL" in stderr


def test_settings_repr_does_not_leak_secret_values() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="123456:fake-token",
        database_url="postgresql://user:hunter2@host/db",
        anthropic_api_key="fake-key",
    )
    assert "123456:fake-token" not in repr(settings)
    assert "hunter2" not in repr(settings)


def test_build_application_registers_start_and_help_commands() -> None:
    application = build_application("123456:fake-token", _fake_deps())
    handlers = application.handlers[0]
    commands: set[str] = set()
    for handler in handlers:
        commands |= getattr(handler, "commands", set())
    assert {"start", "help", "join", "settrip", "cancel"} <= commands


def test_known_commands_includes_balances_mine_settle() -> None:
    assert {"balances", "mine", "settle"} <= _KNOWN_COMMANDS


async def test_balances_in_unbound_group_produces_exactly_one_reply() -> None:
    """Both group 0's own /balances handler and group 1's fallback gate run on
    every update (PTB does not stop at the first matching group); if "balances"
    is missing from _KNOWN_COMMANDS the gate replies too, so an unbound group
    gets UNBOUND_TEXT twice.
    """
    deps = make_deps(InMemoryLedgerStore())  # chat is never bound
    update = MagicMock(spec=Update)
    update.effective_chat = SimpleNamespace(id=-100, type="group")
    message = MagicMock()
    message.text = "/balances"
    message.reply_text = AsyncMock()
    update.effective_message = message
    context = MagicMock()
    context.application.bot_data = {"deps": deps}

    await balances_command(update, context)  # group 0's dedicated handler
    await _unbound_gate(update, context)  # group 1's fallback gate

    message.reply_text.assert_awaited_once_with(UNBOUND_TEXT, parse_mode="HTML")


async def test_unbound_gate_on_closed_trip_replies_trip_is_closed() -> None:
    """sprint 9 item 3: an unrecognised command in a group whose latest trip is
    closed gets the closed-trip line from the shared fallback gate."""
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=-100,
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    trip = await ledger.save_binding(trip)
    await ledger.close_trip(trip)
    deps = make_deps(ledger)
    update = MagicMock(spec=Update)
    update.effective_chat = SimpleNamespace(id=-100, type="group")
    message = MagicMock()
    message.text = "/notarealcommand"
    message.reply_text = AsyncMock()
    update.effective_message = message
    context = MagicMock()
    context.application.bot_data = {"deps": deps}

    await _unbound_gate(update, context)

    message.reply_text.assert_awaited_once_with(CLOSED_TRIP_TEXT, parse_mode="HTML")


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


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="whopaid started",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_allowed_extra_field() -> None:
    formatter = JsonFormatter()
    record = _record(bot_username="whopaidforthisbot", version="abc1234")

    parsed = json.loads(formatter.format(record))

    assert parsed["bot_username"] == "whopaidforthisbot"
    assert parsed["version"] == "abc1234"


def test_json_formatter_drops_disallowed_extra_field() -> None:
    formatter = JsonFormatter()
    record = _record(chat_id=-100, secret_token="should-not-appear")

    parsed = json.loads(formatter.format(record))

    assert "chat_id" not in parsed
    assert "secret_token" not in parsed


def test_json_formatter_redacts_secret_in_an_allowed_extra_field() -> None:
    secret = "123456:fake"
    formatter = JsonFormatter(redact=[secret])
    record = _record(version=secret)

    line = formatter.format(record)

    assert secret not in line
    assert "[REDACTED]" in line


async def test_help_command_replies_with_html_and_mentions_balances() -> None:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.application.bot_data = {"deps": _fake_deps()}

    await help_command(update, context)

    reply_text = update.message.reply_text.await_args.args[0]
    assert reply_text.startswith(HELP_TEXT)
    assert update.message.reply_text.await_args.kwargs == {"parse_mode": "HTML"}
    assert "/balances" in HELP_TEXT


async def test_help_command_footer_shows_bot_version() -> None:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.application.bot_data = {"deps": make_deps(InMemoryLedgerStore(), bot_version="abc1234")}

    await help_command(update, context)

    reply_text = update.message.reply_text.await_args.args[0]
    assert "whopaid abc1234" in reply_text


def test_build_application_registers_error_handler() -> None:
    application = build_application("123456:fake-token", _fake_deps())
    assert len(application.error_handlers) == 1


async def test_error_handler_replies_one_liner_for_ledger_unavailable() -> None:
    update = MagicMock(spec=Update)
    update.update_id = 123
    update.callback_query = None
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()
    context.error = LedgerUnavailable("boom")

    await _error_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML"
    )


async def test_error_handler_says_something_went_wrong_for_other_errors() -> None:
    update = MagicMock(spec=Update)
    update.update_id = 123
    update.callback_query = None
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()
    context.error = ValueError("some other bug")

    await _error_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        SOMETHING_WENT_WRONG_TEXT, parse_mode="HTML"
    )


async def test_error_handler_answers_a_button_press_with_a_toast() -> None:
    update = MagicMock(spec=Update)
    update.update_id = 123
    update.callback_query.answer = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()
    context.error = ValueError("some other bug")

    await _error_handler(update, context)

    update.callback_query.answer.assert_awaited_once_with(SOMETHING_WENT_WRONG_TEXT)
    update.effective_message.reply_text.assert_not_awaited()


async def test_error_handler_handles_missing_update_gracefully() -> None:
    context = MagicMock()
    context.error = LedgerUnavailable("boom")

    await _error_handler(None, context)  # must not raise


def test_build_application_registers_post_init_and_post_shutdown_hooks() -> None:
    application = build_application("123456:fake-token", _fake_deps())
    assert application.post_init is not None
    assert application.post_shutdown is not None


async def test_post_shutdown_hook_logs_whopaid_stopped(caplog: pytest.LogCaptureFixture) -> None:
    application = build_application("123456:fake-token", _fake_deps())
    with caplog.at_level(logging.INFO, logger="app.bot.app"):
        await application.post_shutdown(application)
    assert any(record.message == "whopaid stopped" for record in caplog.records)


def test_stop_signals_includes_sigterm() -> None:
    assert signal.SIGTERM in STOP_SIGNALS


async def test_post_init_startup_error_never_leaks_the_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A DSN with a password embedded in the exception message must never reach
    stderr (sprint 9 review item 1): only a fixed line naming the exception type.
    """
    deps = _fake_deps()
    deps.ledger.connect = AsyncMock(
        side_effect=RuntimeError(
            "could not connect to postgresql://user:hunter2@fake-host/db"
        )
    )
    application = build_application("123456:fake-token", deps)

    with pytest.raises(SystemExit):
        await application.post_init(application)

    captured = capsys.readouterr()
    assert "hunter2" not in captured.err
    assert captured.err.strip() == (
        "Could not reach the database (RuntimeError). Check DATABASE_URL."
    )
