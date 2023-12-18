"""Utilities for context tracing."""

import contextvars
from contextlib import contextmanager
import logging
from time import perf_counter
from typing import Generator


_LOGGER = logging.getLogger(__name__)

# No public API
__all__: list[str] = []


trace: contextvars.ContextVar[list[str]] = contextvars.ContextVar("trace")


@contextmanager
def trace_context(name: str) -> Generator[None, None, None]:
    stack = trace.get([])
    token = trace.set(stack + [name])
    label = " > ".join(stack + [name])
    t1 = perf_counter()
    _LOGGER.debug("[Trace] > %s", label)
    try:
        yield
    finally:
        t2 = perf_counter()
        trace.reset(token)
        _LOGGER.debug("[Trace] < %s (%0.2fs)", label, (t2 - t1))
