"""Shared pytest fixtures and test isolation."""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _reset_herbert_loggers() -> None:
    """Ensure each test starts with the `herbert` logger tree in a clean state.

    `setup_logging()` disables propagation on the `herbert` logger, which would
    otherwise leak across tests and cause caplog to miss warnings. This fixture
    resets handlers and propagation so each test sees a fresh slate.
    """
    for name in list(logging.Logger.manager.loggerDict):
        if name == "herbert" or name.startswith("herbert."):
            lg = logging.getLogger(name)
            lg.handlers = []
            lg.propagate = True
            lg.setLevel(logging.NOTSET)
