"""Tests for scripts/export_fly_secrets.py: the dotenv-based Fly secrets exporter.

Runs the script as a subprocess against a throwaway temp `.env` (never the repo's real
one), using `sys.executable` -- the same interpreter pytest itself runs under via
`uv run pytest`, so `app` and `dotenv` are already importable without needing a `uv run`
wrapper around the subprocess.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_fly_secrets.py"

FAKE_ENV = (
    "TELEGRAM_BOT_TOKEN=123456:fake-token\n"
    'GOOGLE_SERVICE_ACCOUNT_JSON="{\\"client_email\\": \\"fake@x.iam.gserviceaccount.com\\", '
    '\\"private_key\\": \\"-----BEGIN KEY-----\\\\nabc\\\\n-----END KEY-----\\\\n\\"}"\n'
    "ANTHROPIC_API_KEY=sk-fake-key\n"
    "WHOPAID_CONTROL_SHEET_ID=fake-sheet-id\n"
    "LOG_LEVEL=DEBUG\n"
    "BOT_VERSION=abc1234\n"
)

EXPECTED_JSON_VALUE = (
    '{"client_email": "fake@x.iam.gserviceaccount.com", '
    '"private_key": "-----BEGIN KEY-----\\nabc\\n-----END KEY-----\\n"}'
)


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / ".env").write_text(FAKE_ENV)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_exports_exact_name_value_lines_in_settings_order(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "TELEGRAM_BOT_TOKEN=123456:fake-token",
        f"GOOGLE_SERVICE_ACCOUNT_JSON={EXPECTED_JSON_VALUE}",
        "ANTHROPIC_API_KEY=sk-fake-key",
        "WHOPAID_CONTROL_SHEET_ID=fake-sheet-id",
    ]


def test_names_flag_prints_names_only(tmp_path: Path) -> None:
    result = _run(tmp_path, "--names")

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "TELEGRAM_BOT_TOKEN",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "ANTHROPIC_API_KEY",
        "WHOPAID_CONTROL_SHEET_ID",
    ]


def test_log_level_and_bot_version_are_excluded(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert "LOG_LEVEL" not in result.stdout
    assert "BOT_VERSION" not in result.stdout
    assert "DEBUG" not in result.stdout
    assert "abc1234" not in result.stdout


def test_missing_required_variable_exits_1_naming_it(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=123456:fake-token\n"
        "GOOGLE_SERVICE_ACCOUNT_JSON={}\n"
        "ANTHROPIC_API_KEY=sk-fake-key\n"
        # WHOPAID_CONTROL_SHEET_ID missing.
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "WHOPAID_CONTROL_SHEET_ID" in result.stderr
    assert result.stdout == ""
