"""Tests for config module."""

import pytest

from djobs.core.config import Config


class TestConfigDefaults:
    def test_default_values(self) -> None:
        cfg = Config()
        assert cfg.db_path == "djobs.db"
        assert cfg.log_level == "INFO"
        assert cfg.log_format == "json"
        assert cfg.worker_id == "worker-1"

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DJOBS_DB_PATH", "/tmp/test.db")
        monkeypatch.setenv("DJOBS_LOG_LEVEL", "DEBUG")

        cfg = Config.from_env()

        assert cfg.db_path == "/tmp/test.db"
        assert cfg.log_level == "DEBUG"
