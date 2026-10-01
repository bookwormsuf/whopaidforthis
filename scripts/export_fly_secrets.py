"""Exports the Fly secret variables read from `.env`, for `scripts/set-fly-secrets.sh`.

Uses `dotenv.dotenv_values(".env")` -- the same parser `pydantic_settings.BaseSettings`
uses to read `.env` -- so escaped quotes, `export ` prefixes and inline comments behave
identically to how the running bot would read them. Prints `NAME=value` lines to
stdout, one per required variable, in `Settings`' declared field order, and nothing
else to stdout. Never prints a value to stderr or anywhere else.

Required variables are derived from `Settings`: every `SecretStr` field, plus
`whopaid_control_sheet_id` (not a `SecretStr`, but still required and still sensitive
enough to live only in `.env`/Fly secrets, never in git). Adding a field to `Settings`
later therefore updates this list automatically, with no second place to edit.

LOG_LEVEL and BOT_VERSION are deliberately excluded: neither is a secret, and setting
BOT_VERSION as a Fly secret would override the image's own `ENV BOT_VERSION=...` and
freeze the version shown in `/help` across every later deploy. `LOG_LEVEL` can still be
set with `fly secrets set LOG_LEVEL=DEBUG` by hand if ever needed (see README).

Validates by constructing `Settings()` (via `load_settings()`) first, so a broken or
incomplete `.env` fails loudly, before anything is read for export.

Usage:
    uv run python scripts/export_fly_secrets.py            # NAME=value lines
    uv run python scripts/export_fly_secrets.py --names     # NAME only, one per line
"""

from __future__ import annotations

import os
import sys

from dotenv import dotenv_values
from pydantic import SecretStr

from app.config.settings import Settings, load_settings

# Overrides which dotenv file is read for the export step. Production never sets this
# -- it always means the repo root's own `.env`. Tests (and scripts/set-fly-secrets.sh's
# own --dry-run smoke test against a scratch file) use it to avoid ever touching the
# real .env.
ENV_FILE = os.environ.get("WHOPAID_ENV_FILE", ".env")


def required_names() -> list[str]:
    """Env var names to export: Settings' SecretStr fields plus WHOPAID_CONTROL_SHEET_ID.

    Walks `Settings.model_fields` in declared order so the output order is stable and
    adding a new SecretStr field to `Settings` is enough to pick it up here too.
    """
    return [
        name.upper()
        for name, field in Settings.model_fields.items()
        if field.annotation is SecretStr or name == "whopaid_control_sheet_id"
    ]


def main() -> None:
    # Fail before reading anything for export if .env is missing or invalid.
    # (Relative to the current working directory, same as Settings' own env_file=".env".)
    load_settings()

    values = dotenv_values(ENV_FILE)
    names_only = "--names" in sys.argv[1:]

    lines: list[str] = []
    for name in required_names():
        value = values.get(name)
        if not value:
            print(f"Missing required environment variable: {name}", file=sys.stderr)
            raise SystemExit(1)
        lines.append(name if names_only else f"{name}={value}")

    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
