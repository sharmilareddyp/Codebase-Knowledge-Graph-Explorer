"""Logging setup that keeps credentials out of log records."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure a single process-wide log format."""

    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        numeric = logging.INFO
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def redact(message: str, secrets: list[str]) -> str:
    """Replace known secret values and truncate the message."""

    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted[:300]
