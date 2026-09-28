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


class JsonFormatter(logging.Formatter):
    """Formats each log record as one JSON object per line, redacting given secrets."""

    def __init__(self, redact: Sequence[str] = ()) -> None:
        super().__init__()
        self._redact = [value for value in redact if value]

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
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
