"""JSON-lines logging setup.

httpx/telegram request logs (which can include the bot token in a URL) are suppressed by
raising those loggers to WARNING, and any redact strings passed in are stripped from the
final formatted line, but nothing in this sprint logs message text or image bytes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

# Allowlist of `extra=` keys a log call may attach as structured fields. Anything else
# passed via `extra={...}` is dropped rather than emitted, so a careless `extra=` can't
# leak an unreviewed field into the logs. Grepped from every `extra=` call site in app/
# (currently only app/bot/app.py's startup line) plus bot_username/version themselves.
EXTRA_FIELDS = frozenset({"bot_username", "version", "input_tokens", "output_tokens"})


class JsonFormatter(logging.Formatter):
    """Formats each log record as one JSON object per line, redacting given secrets."""

    def __init__(self, redact: Sequence[str] = ()) -> None:
        super().__init__()
        self._redact = [value for value in redact if value]

    def format(self, record: logging.LogRecord) -> str:
        # Extras go in first so the core fields set below always win a key collision:
        # an extra can never overwrite ts/level/logger/msg/exc.
        payload: dict[str, object] = {}
        for key, value in record.__dict__.items():
            if key in EXTRA_FIELDS:
                payload[key] = value
        payload["ts"] = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        payload["level"] = record.levelname
        payload["logger"] = record.name
        payload["msg"] = record.getMessage()
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # No `default=str`: a non-serialisable extra should raise so it's caught in
        # tests, not silently stringified into the logs.
        line = json.dumps(payload)
        for secret in self._redact:
            line = line.replace(secret, "[REDACTED]")
        return line


def configure_logging(log_level: str, redact: Sequence[str] = ()) -> None:
    """Configure stdlib logging to emit JSON lines at the given level.

    Sets httpx and telegram loggers to WARNING so their request logs (which can carry
    the bot token in a URL) are suppressed, and redacts any given secret strings from
    whatever does get formatted.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(redact=redact))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
