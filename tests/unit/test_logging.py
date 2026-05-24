"""Tests for structured logging setup."""

import logging

from djobs.observability.logging import get_logger, setup_logging


class TestLogging:
    def test_json_format(self) -> None:
        setup_logging(level="DEBUG", fmt="json")
        logger = get_logger("test")
        logger.info("hello")

        # Just verify logger is configured and doesn't crash
        assert logger.name == "djobs.test"
        assert logger.getEffectiveLevel() == logging.DEBUG

    def test_text_format(self) -> None:
        setup_logging(level="INFO", fmt="text")
        logger = get_logger("test2")
        logger.info("text mode")
        assert logger.name == "djobs.test2"

    def test_get_logger_namespace(self) -> None:
        logger = get_logger("worker")
        assert logger.name == "djobs.worker"
