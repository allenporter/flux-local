from collections.abc import Generator
import logging
import tempfile
import pathlib
import pytest
from flux_local import context

_LOGGER = logging.getLogger(__name__)

# Global collector for the whole session
SESSION_COLLECTOR = context.TraceCollector()


@pytest.fixture(scope="session")
def global_helm_cache() -> Generator[pathlib.Path, None, None]:
    """Create a session-wide helm cache directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield pathlib.Path(tmp_dir)


@pytest.fixture(autouse=True)
def setup_helm_cache(global_helm_cache: pathlib.Path) -> Generator[None, None, None]:
    """Set the global helm cache directory for the context."""
    token = context.HELM_CACHE_DIR.set(global_helm_cache)
    yield
    context.HELM_CACHE_DIR.reset(token)


@pytest.fixture(autouse=True)
def trace_capture() -> Generator[None, None, None]:
    """Capture traces for each test and add them to the session collector."""
    with context.get_trace_collector() as collector:
        yield
        for name, duration in collector.timings.items():
            SESSION_COLLECTOR.add(name, duration)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Print the trace summary at the end of the session."""
    # We use a printer instead of logger to ensure it's visible in the output w/o -s
    if not SESSION_COLLECTOR.timings:
        return

    print("\n\n" + "=" * 20 + " PERFORMANCE TRACE SUMMARY " + "=" * 20)
    for name, duration in sorted(
        SESSION_COLLECTOR.timings.items(), key=lambda x: x[1], reverse=True
    ):
        count = SESSION_COLLECTOR.counts[name]
        print(
            f" - {name:<40}: {duration:>6.2f}s (count: {count:>3}, avg: {duration / count:>6.2f}s)"
        )
    print("=" * 67 + "\n")
