"""Logger ↔ event bus integration: log records are published as LogLine events."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from herbert.events import AsyncEventBus, LogLine
from herbert.logging import BusHandler, setup_logging


class TestBusHandler:
    async def test_log_record_flows_to_bus_as_logline(self) -> None:
        bus = AsyncEventBus()
        handler = BusHandler(bus)
        logger = logging.getLogger("herbert.test_bus_flow")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        async with bus.subscribe() as sub:
            logger.info("hello %s", "world")
            event = await asyncio.wait_for(sub.receive(), timeout=1.0)
            assert isinstance(event, LogLine)
            assert event.level == "INFO"
            assert "hello world" in event.line

    async def test_handler_redaction_applied(self) -> None:
        bus = AsyncEventBus()
        handler = BusHandler(bus)
        logger = logging.getLogger("herbert.test_bus_redact")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        async with bus.subscribe() as sub:
            logger.info("loaded sk-ant-api03-abc123xyz999 ok")
            event = await asyncio.wait_for(sub.receive(), timeout=1.0)
            assert "sk-ant" not in event.line
            assert "[REDACTED]" in event.line


class TestSetupLogging:
    def test_setup_logging_returns_logger_with_handlers(self, tmp_path: Path) -> None:
        logger = setup_logging(log_path=tmp_path / "log.log", level="DEBUG")
        assert logger.name == "herbert"
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) >= 2  # console + file

    def test_setup_logging_idempotent(self, tmp_path: Path) -> None:
        setup_logging(log_path=tmp_path / "log.log")
        first_handlers = len(logging.getLogger("herbert").handlers)
        setup_logging(log_path=tmp_path / "log.log")
        second_handlers = len(logging.getLogger("herbert").handlers)
        assert first_handlers == second_handlers  # no duplication

    def test_setup_logging_with_bus_adds_bus_handler(self, tmp_path: Path) -> None:
        bus = AsyncEventBus()
        logger = setup_logging(log_path=tmp_path / "log.log", bus=bus)
        handler_types = {type(h).__name__ for h in logger.handlers}
        assert "BusHandler" in handler_types
