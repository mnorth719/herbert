"""Structured logging setup + RedactingFilter that scrubs secrets before writing."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

# Known secret patterns. Tuned to avoid false positives on short hyphenated words.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Anthropic: sk-ant-<20+ chars>, including underscores and hyphens inside the body
    (re.compile(r"sk-ant-[\w\-\.]{10,}", re.IGNORECASE), "[REDACTED]"),
    # Generic sk-* (OpenAI, etc.) — require 20+ chars after the prefix to avoid "sk-blog" style false positives
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED]"),
    # ElevenLabs: sk_<anything chunky>
    (re.compile(r"sk_[A-Za-z0-9_\-]{10,}"), "[REDACTED]"),
    # XI keys
    (re.compile(r"xi_[A-Za-z0-9_\-]{8,}"), "[REDACTED]"),
    # Bearer tokens in Authorization header
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE), r"\1[REDACTED]"),
    # URL query param values for token/key/bearer
    (
        re.compile(r"([?&](?:token|key|bearer)=)([^&\s]+)", re.IGNORECASE),
        r"\1[REDACTED]",
    ),
]

# Structured log-record fields that should always be redacted if present
_SENSITIVE_FIELD_NAMES = {"api_key", "api_keys", "token", "tokens", "bearer", "authorization", "secret"}


class RedactingFilter(logging.Filter):
    """Scrubs known secret patterns from log messages AND sensitive fields from record attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitize the rendered message
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            # Replace the record so downstream formatters see the scrubbed content
            record.msg = redacted
            record.args = None

        # Sanitize sensitive structured fields
        for attr_name in list(vars(record)):
            if attr_name.lower() in _SENSITIVE_FIELD_NAMES:
                setattr(record, attr_name, "[REDACTED]")
        return True


class JsonFormatter(logging.Formatter):
    """Emits one JSON line per record. Intended for the file handler."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Pass through structured extras (excluding stdlib fields)
        stdlib_fields = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message",
        }
        for key, value in record.__dict__.items():
            if key in stdlib_fields or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(log_path: Path, level: str = "INFO", backup_count: int = 7) -> logging.Logger:
    """Initialize the root Herbert logger. Idempotent — callable multiple times safely."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("herbert")
    root.setLevel(level.upper())
    # Clear any handlers from previous init so tests / restarts don't duplicate output
    root.handlers = []

    redactor = RedactingFilter()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s  %(name)s  %(message)s"))
    console.addFilter(redactor)
    root.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(redactor)
    root.addHandler(file_handler)

    root.propagate = False
    return root
