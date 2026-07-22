"""Structured logging setup.

Supports two formats:
- json: one JSON object per line (for production / log aggregation).
- text: human-readable colored output (for local dev).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Allow extra structured fields via record.__dict__
        for key in ("job_id", "worker_id", "job_type", "attempt", "correlation_id", "duration_s"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure root logger for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        fmt: 'json' for structured JSON lines, 'text' for human-readable.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on repeated calls
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
        )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger under the djobs namespace."""
    return logging.getLogger(f"djobs.{name}")
