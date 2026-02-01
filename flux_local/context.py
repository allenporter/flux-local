"""Utilities for context tracing."""

from contextvars import ContextVar
from contextlib import contextmanager
import logging
from time import perf_counter
from typing import Generator
from dataclasses import dataclass, field


import pathlib

_LOGGER = logging.getLogger(__name__)

# No public API
__all__: list[str] = [
    "trace_context",
    "TraceCollector",
    "get_trace_collector",
    "HELM_CACHE_DIR",
]


HELM_CACHE_DIR: ContextVar[pathlib.Path | None] = ContextVar(
    "helm_cache_dir", default=None
)


@dataclass
class TraceCollector:
    """Collects trace timings."""

    timings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, duration: float) -> None:
        """Add a timing for a trace."""
        self.timings[name] = self.timings.get(name, 0) + duration
        self.counts[name] = self.counts.get(name, 0) + 1

    def report(self, logger: logging.Logger) -> None:
        """Report the collected timings."""
        if not self.timings:
            return
        logger.info("Trace Summary:")
        # Sort by total duration descending
        for name, duration in sorted(
            self.timings.items(), key=lambda x: x[1], reverse=True
        ):
            count = self.counts[name]
            logger.info(
                " - %-40s: %6.2fs (count: %3d, avg: %6.2fs)",
                name,
                duration,
                count,
                duration / count,
            )


trace: ContextVar[list[str]] = ContextVar("trace", default=[])
collector: ContextVar[TraceCollector | None] = ContextVar("collector", default=None)


@contextmanager
def get_trace_collector() -> Generator[TraceCollector, None, None]:
    """Provide a trace collector for the context."""
    c = TraceCollector()
    token = collector.set(c)
    try:
        yield c
    finally:
        collector.reset(token)


@contextmanager
def trace_context(name: str) -> Generator[None, None, None]:
    stack = trace.get()
    token = trace.set(stack + [name])
    label = " > ".join(stack + [name])
    t1 = perf_counter()
    _LOGGER.debug("[Trace] > %s", label)
    try:
        yield
    finally:
        t2 = perf_counter()
        duration = t2 - t1
        trace.reset(token)
        _LOGGER.debug("[Trace] < %s (%0.2fs)", label, duration)
        if c := collector.get():
            c.add(name, duration)
