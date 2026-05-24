"""Minimal configuration loader using dataclass + environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    """Application configuration.

    Reads from environment variables with sensible defaults.
    Prefix: DJOBS_
    """

    db_path: str = field(default_factory=lambda: os.getenv("DJOBS_DB_PATH", "djobs.db"))
    log_level: str = field(default_factory=lambda: os.getenv("DJOBS_LOG_LEVEL", "INFO"))
    log_format: str = field(default_factory=lambda: os.getenv("DJOBS_LOG_FORMAT", "json"))
    worker_id: str = field(default_factory=lambda: os.getenv("DJOBS_WORKER_ID", "worker-1"))

    @classmethod
    def from_env(cls) -> Config:
        """Build config from current environment variables."""
        return cls()
