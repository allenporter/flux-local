"""Utilities for context tracing."""

import contextvars
from contextlib import contextmanager
import logging
from typing import Generator

_LOGGER = logging.getLogger(__name__)

# No public API
__all__: list[str] = []


trace: contextvars.ContextVar[list[str]] = contextvars.ContextVar("trace")


@contextmanager
def trace_context(name: str) -> Generator[None, None, None]:
    stack = trace.get([])
    token = trace.set(stack + [name])
    _LOGGER.debug("[Trace] > %s", name)
    try:
        yield
    finally:
        trace.reset(token)
        _LOGGER.debug("[Trace] < %s", name)
