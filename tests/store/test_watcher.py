"""Tests for the DependencyWaiter component."""

import asyncio
import pytest
import logging
from unittest.mock import AsyncMock, MagicMock
from typing import Coroutine, Any

from flux_local.manifest import NamedResource
from flux_local.store.status import Status, StatusInfo
from flux_local.store.store import Store
from flux_local.store.watcher import (
    DependencyWaiter,
    DependencyState,
    DependencyResolutionEvent,
)
from flux_local.task.service import TaskService
from flux_local.exceptions import ResourceFailedError


_LOGGER = logging.getLogger(__name__)


def rn(
    name: str, kind: str = "Kustomization", namespace: str = "default"
) -> NamedResource:
    """Create a NamedResource for tests."""
    return NamedResource(kind=kind, name=name, namespace=namespace)


@pytest.fixture
def mock_store() -> AsyncMock:
    """Provides a mock Store instance that hangs indefinitely by default."""
    store = AsyncMock(spec=Store)

    async def default_watch_ready_behavior(*args: Any, **kwargs: Any) -> StatusInfo:
        """Default behavior for watch_ready: hangs indefinitely."""
        await asyncio.Future()  # Hang indefinitely
        # The following lines are to satisfy the async generator type if needed,
        # but watch_ready is an async function, not an async generator.
        # For an async function, this part is effectively unreachable.
        # However, if watch_ready were an async generator, it would be:
        # if False:
        #     yield
        raise asyncio.InvalidStateError("Should not be reached in mock")

    store.watch_ready = AsyncMock(side_effect=default_watch_ready_behavior)
    return store


@pytest.fixture
def mock_task_service() -> MagicMock:
    """Provides a basic mock TaskService instance that runs tasks."""
    service = MagicMock(spec=TaskService)
    created_mock_task_wrappers: list[MagicMock] = []
    actual_tasks: list[asyncio.Task[Any]] = []

    service.created_mock_tasks = created_mock_task_wrappers
    service.actual_asyncio_tasks = actual_tasks

    def create_task_side_effect(coro: Coroutine[Any, Any, Any]) -> MagicMock:
        """Side effect for create_task that creates, runs an asyncio.Task, and returns a mock wrapper."""
        loop = asyncio.get_running_loop()
        actual_task: asyncio.Task[Any] = loop.create_task(coro)
        actual_tasks.append(actual_task)

        # Create a mock wrapper around the actual task for easier mocking/inspection
        mock_task_wrapper = MagicMock(spec=asyncio.Task)
        mock_task_wrapper.cancel = actual_task.cancel
        mock_task_wrapper.done = lambda: actual_task.done()
        mock_task_wrapper.cancelled = lambda: actual_task.cancelled()
        mock_task_wrapper.actual_task = actual_task  # For direct manipulation if needed
        mock_task_wrapper.coro = coro  # Store coro for inspection if needed

        created_mock_task_wrappers.append(mock_task_wrapper)
        return mock_task_wrapper

    service.create_task = MagicMock(side_effect=create_task_side_effect)
    return service


@pytest.fixture
def parent_resource_id() -> NamedResource:
    """Provides a default parent NamedResource for the waiter."""
    return rn("parent", "FluxSystem", "flux-system")


@pytest.fixture
def waiter(
    mock_store: AsyncMock,
    mock_task_service: MagicMock,
    parent_resource_id: NamedResource,
) -> DependencyWaiter:
    """Provides a DependencyWaiter instance with basic mocks."""
    return DependencyWaiter(
        store=mock_store,
        task_service=mock_task_service,
        parent_resource_id=parent_resource_id,
    )


@pytest.fixture
def cancellable_mock_task_service() -> MagicMock:
    """Provides a mock TaskService that creates truly cancellable asyncio.Tasks."""
    service = MagicMock(spec=TaskService)
    created_mock_tasks: list[MagicMock] = []
    actual_asyncio_tasks: list[asyncio.Task[Any]] = []
    service.created_mock_tasks = created_mock_tasks  # For inspection
    service.actual_asyncio_tasks = actual_asyncio_tasks  # For inspection

    def create_task_side_effect(coro: Coroutine[Any, Any, Any]) -> MagicMock:
        """Side effect for create_task that creates a real, cancellable asyncio.Task."""
        loop = asyncio.get_running_loop()
        actual_task: asyncio.Task[Any] = loop.create_task(coro)
        actual_asyncio_tasks.append(actual_task)

        # Create a mock wrapper around the actual task for easier mocking/inspection
        mock_task_wrapper = MagicMock(spec=asyncio.Task)
        mock_task_wrapper.cancel = actual_task.cancel
        mock_task_wrapper.done = lambda: actual_task.done()
        mock_task_wrapper.cancelled = lambda: actual_task.cancelled()
        mock_task_wrapper.actual_task = actual_task  # For direct manipulation if needed

        created_mock_tasks.append(mock_task_wrapper)
        return mock_task_wrapper

    service.create_task = MagicMock(side_effect=create_task_side_effect)
    return service


@pytest.fixture
def cancellable_waiter(
    mock_store: AsyncMock,
    cancellable_mock_task_service: MagicMock,
    parent_resource_id: NamedResource,
) -> DependencyWaiter:
    """Provides a DependencyWaiter instance with a cancellable TaskService."""
    return DependencyWaiter(
        store=mock_store,
        task_service=cancellable_mock_task_service,
        parent_resource_id=parent_resource_id,
        timeout_seconds=0.1,  # Default short timeout for tests
    )


async def test_initialization_no_dependencies(waiter: DependencyWaiter) -> None:
    """Test waiter initializes correctly with no dependencies."""
    summary = waiter.get_summary()
    assert summary.pending_count == 0
    assert summary.ready_count == 0
    assert summary.failed_count == 0
    assert not summary.all_ready
    assert not summary.any_failed

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)
    assert not events
    # Verify summary again after watch (should still be empty)
    summary_after = waiter.get_summary()
    assert summary_after.pending_count == 0
    assert not summary_after.all_ready


async def test_add_single_dependency(waiter: DependencyWaiter) -> None:
    """Test adding a single dependency updates the summary correctly."""
    dep1 = rn("dep1")
    waiter.add(dep1)
    summary = waiter.get_summary()
    assert summary.pending_count == 1
    assert summary.pending_dependencies == [dep1]
    assert not summary.all_ready


async def test_add_multiple_dependencies(waiter: DependencyWaiter) -> None:
    """Test adding multiple dependencies updates the summary correctly."""
    dep1, dep2 = rn("dep1"), rn("dep2")
    waiter.add(dep1)
    waiter.add(dep2)
    summary = waiter.get_summary()
    assert summary.pending_count == 2
    assert set(summary.pending_dependencies) == {dep1, dep2}
    assert not summary.all_ready


async def test_single_dependency_becomes_ready(
    waiter: DependencyWaiter, mock_store: AsyncMock
) -> None:
    """Test a single dependency that resolves to READY."""
    dep1 = rn("dep1")
    status_dep1 = StatusInfo(status=Status.READY)

    async def watch_ready_side_effect(res_id: NamedResource) -> StatusInfo:
        if res_id == dep1:
            await asyncio.sleep(0.01)  # Simulate some work
            return status_dep1
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_side_effect)
    waiter.add(dep1)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 1
    event = events[0]
    assert event.resource_id == dep1
    assert event.state == DependencyState.READY
    assert event.status_info == status_dep1
    assert event.success
    assert not event.failure

    summary = waiter.get_summary()
    assert summary.all_ready
    assert summary.ready_count == 1
    assert summary.pending_count == 0


async def test_multiple_dependencies_all_become_ready(
    waiter: DependencyWaiter, mock_store: AsyncMock, mock_task_service: MagicMock
) -> None:
    """Test multiple dependencies all resolving to READY."""
    dep1, dep2 = rn("dep1"), rn("dep2", "GitRepository")
    status_dep1 = StatusInfo(status=Status.READY)
    status_dep2 = StatusInfo(status=Status.READY)

    async def watch_ready_side_effect(res_id: NamedResource) -> StatusInfo:
        if res_id == dep1:
            await asyncio.sleep(0.02)  # dep1 takes longer
            return status_dep1
        elif res_id == dep2:
            await asyncio.sleep(0.01)
            return status_dep2
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_side_effect)

    waiter.add(dep1)
    waiter.add(dep2)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 2
    event_map = {e.resource_id: e for e in events}
    assert event_map[dep1].state == DependencyState.READY
    assert event_map[dep1].status_info == status_dep1
    assert event_map[dep2].state == DependencyState.READY
    assert event_map[dep2].status_info == status_dep2

    summary = waiter.get_summary()
    assert summary.all_ready
    assert summary.ready_count == 2
    assert summary.pending_count == 0
    assert mock_task_service.create_task.call_count == 2


async def test_single_dependency_fails_with_exception(
    waiter: DependencyWaiter, mock_store: AsyncMock
) -> None:
    """Test a single dependency that fails with ResourceFailedError."""
    dep1 = rn("dep1_fail_exc")
    error_message = "Resource blew up"
    fail_status = StatusInfo(status=Status.FAILED, error=error_message)

    async def watch_ready_side_effect(res_id: NamedResource) -> StatusInfo:
        if res_id == dep1:
            await asyncio.sleep(0.01)
            raise ResourceFailedError(
                resource_name=dep1.namespaced_name,
                message=error_message,
            )
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_side_effect)
    waiter.add(dep1)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 1
    event = events[0]
    assert event.resource_id == dep1
    assert event.state == DependencyState.FAILED
    assert event.error_message == error_message
    assert event.status_info == fail_status
    assert not event.success
    assert event.failure

    summary = waiter.get_summary()
    assert not summary.all_ready
    assert summary.any_failed
    assert summary.failed_count == 1
    assert summary.ready_count == 0
    assert summary.failed_dependencies[0] == event


async def test_multiple_dependencies_one_fails_others_ready(
    waiter: DependencyWaiter, mock_store: AsyncMock
) -> None:
    """Test a mix of successful and failed dependencies."""
    dep_ok1 = rn("dep_ok1")
    dep_fail = rn("dep_failing")
    dep_ok2 = rn("dep_ok2_late")

    status_ok1 = StatusInfo(status=Status.READY)
    status_ok2 = StatusInfo(status=Status.READY)
    fail_status_info = StatusInfo(status=Status.FAILED, error="Dep failed")

    async def watch_ready_behaviors(res_id: NamedResource) -> StatusInfo:
        if res_id == dep_ok1:
            return status_ok1
        elif res_id == dep_fail:
            await asyncio.sleep(0.01)
            raise ResourceFailedError(
                res_id.namespaced_name,
                "Dep failed",
            )
        elif res_id == dep_ok2:
            await asyncio.sleep(0.02)
            return status_ok2
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_behaviors)

    waiter.add(dep_ok1)
    waiter.add(dep_fail)
    waiter.add(dep_ok2)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 3
    event_map = {e.resource_id: e for e in events}
    assert event_map[dep_ok1].state == DependencyState.READY
    assert event_map[dep_fail].state == DependencyState.FAILED
    assert event_map[dep_fail].status_info == fail_status_info
    assert event_map[dep_ok2].state == DependencyState.READY

    summary = waiter.get_summary()
    assert not summary.all_ready
    assert summary.any_failed
    assert summary.ready_count == 2
    assert summary.failed_count == 1
    assert summary.pending_count == 0
    assert len(summary.failed_dependencies) == 1
    assert summary.failed_dependencies[0].resource_id == dep_fail


async def test_single_dependency_times_out(
    cancellable_waiter: DependencyWaiter,
    mock_store: AsyncMock,  # Use cancellable for timeout test
) -> None:
    """Test a single dependency that times out."""
    waiter = cancellable_waiter  # Use the one with short timeout
    dep_timeout = rn("dep_timeout")

    async def hang_forever(res_id: NamedResource) -> StatusInfo:
        if res_id == dep_timeout:
            await asyncio.Future()  # Hang
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=hang_forever)
    waiter.add(dep_timeout)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():  # Relies on waiter's internal timeout
        events.append(event)

    assert len(events) == 1
    event = events[0]
    assert event.resource_id == dep_timeout
    assert event.state == DependencyState.TIMEOUT
    assert (
        "Timeout waiting for" in event.error_message if event.error_message else False
    )
    assert not event.success
    assert event.failure

    summary = waiter.get_summary()
    assert not summary.all_ready
    assert summary.any_failed
    assert summary.failed_count == 1  # Consolidated
    assert summary.ready_count == 0
    assert (
        summary.failed_dependencies[0] == event
    )  # Timeouts are included in failed_dependencies


async def test_multiple_dependencies_one_times_out_others_resolve(
    cancellable_waiter: DependencyWaiter, mock_store: AsyncMock
) -> None:
    """Test mix of ready, failed, and timed-out dependencies."""
    waiter = cancellable_waiter  # Use short timeout
    dep_ready = rn("dep_ok_multi")
    dep_timeout = rn("dep_timeout_multi")
    dep_failed = rn("dep_fail_multi")

    status_ready = StatusInfo(status=Status.READY)

    async def watch_ready_behaviors(res_id: NamedResource) -> StatusInfo:
        if res_id == dep_ready:
            return status_ready
        elif res_id == dep_timeout:
            await asyncio.Future()  # Hang
        elif res_id == dep_failed:
            await asyncio.sleep(0.005)  # Fail quickly but not instantly
            raise ResourceFailedError(
                res_id.namespaced_name,
                "Fail Multi",
            )
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_behaviors)

    waiter.add(dep_ready)
    waiter.add(dep_timeout)
    waiter.add(dep_failed)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 3
    event_map = {e.resource_id: e for e in events}

    assert event_map[dep_ready].state == DependencyState.READY
    assert event_map[dep_timeout].state == DependencyState.TIMEOUT
    assert event_map[dep_failed].state == DependencyState.FAILED

    summary = waiter.get_summary()
    assert not summary.all_ready
    assert summary.any_failed
    assert summary.ready_count == 1
    assert summary.failed_count == 2  # Consolidated (failed + timeout)
    assert len(summary.failed_dependencies) == 2  # Failed and Timeout
    failed_ids = {fd.resource_id for fd in summary.failed_dependencies}
    assert {dep_failed, dep_timeout} == failed_ids


async def test_cancel_pending_watches_while_watching(
    cancellable_waiter: DependencyWaiter,
    mock_store: AsyncMock,
    cancellable_mock_task_service: MagicMock,
) -> None:
    """Test cancelling pending watches while the main watch loop is active."""
    waiter = cancellable_waiter
    dep_hang = rn("dep_hang_cancel")
    dep_quick_ready = rn("dep_quick_ready_cancel")

    status_ready = StatusInfo(status=Status.READY)
    event_ready_processed = asyncio.Event()

    async def watch_ready_side_effect(res_id: NamedResource) -> StatusInfo:
        if res_id == dep_hang:
            await asyncio.Future()  # Hang indefinitely
        elif res_id == dep_quick_ready:
            return status_ready
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_side_effect)

    waiter.add(dep_hang)
    waiter.add(dep_quick_ready)

    events: list[DependencyResolutionEvent] = []
    watch_task_completed = asyncio.Event()

    async def consume_events() -> None:
        async for event in waiter.watch():
            events.append(event)
            if (
                event.resource_id == dep_quick_ready
                and event.state == DependencyState.READY
            ):
                event_ready_processed.set()
        watch_task_completed.set()

    consumer_task = asyncio.create_task(consume_events())

    # Wait for the quick_ready dependency to be processed
    await asyncio.wait_for(event_ready_processed.wait(), timeout=1)
    assert len(events) == 1
    assert events[0].resource_id == dep_quick_ready

    # Now cancel pending watches (dep_hang should be pending)
    await waiter.cancel_pending_watches()

    # The consumer_task should now complete, yielding the CANCELLED event for dep_hang
    await asyncio.wait_for(watch_task_completed.wait(), timeout=1)
    await consumer_task

    assert len(events) == 2
    event_map = {e.resource_id: e for e in events}
    assert event_map[dep_quick_ready].state == DependencyState.READY
    assert event_map[dep_hang].state == DependencyState.CANCELLED

    summary = waiter.get_summary()
    assert not summary.all_ready
    assert summary.any_failed  # CANCELLED is a failure state for any_failed
    assert summary.ready_count == 1
    assert summary.failed_count == 1  # Consolidated (cancelled)
    assert summary.pending_count == 0


async def test_add_same_dependency_twice_is_ignored(
    waiter: DependencyWaiter, mock_store: AsyncMock, mock_task_service: MagicMock
) -> None:
    """Test that adding the same dependency more than once is a no-op after the first."""
    dep1 = rn("dep_dup")
    status_dep1 = StatusInfo(status=Status.READY)

    async def watch_ready_side_effect(res_id: NamedResource) -> StatusInfo:
        if res_id == dep1:
            return status_dep1
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_side_effect)

    waiter.add(dep1)
    with pytest.raises(ValueError, match="already added"):
        waiter.add(dep1)  # Add same again

    summary_before_watch = waiter.get_summary()
    assert summary_before_watch.pending_count == 1  # Not 2

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 1
    assert events[0].resource_id == dep1
    assert mock_task_service.create_task.call_count == 1  # Task created only once

    summary_after_watch = waiter.get_summary()
    assert summary_after_watch.all_ready
    assert summary_after_watch.ready_count == 1


async def test_watch_with_zero_timeout_for_waiter(
    mock_store: AsyncMock,
    parent_resource_id: NamedResource,
    cancellable_mock_task_service: MagicMock,
) -> None:
    """Test that a waiter-level timeout of 0.0 causes immediate timeout for hanging deps."""
    # Create waiter with 0.0s timeout
    waiter = DependencyWaiter(
        store=mock_store,
        task_service=cancellable_mock_task_service,
        parent_resource_id=parent_resource_id,
        timeout_seconds=0.0,
    )
    dep_timeout_imm = rn("dep_timeout_zero")

    async def hang_forever(res_id: NamedResource) -> StatusInfo:
        if res_id == dep_timeout_imm:
            await asyncio.Future()  # Hang
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=hang_forever)
    waiter.add(dep_timeout_imm)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 1
    event = events[0]
    assert event.resource_id == dep_timeout_imm
    assert event.state == DependencyState.TIMEOUT
    assert (
        "Timeout waiting for" in event.error_message if event.error_message else False
    )

    summary = waiter.get_summary()
    assert summary.failed_count == 1  # Consolidated (timeout)
    assert summary.any_failed


async def test_event_details_are_correct_for_all_resolution_types(
    cancellable_waiter: DependencyWaiter,
    mock_store: AsyncMock,  # cancellable for timeout
) -> None:
    """Test that event details (message, status_info) are correctly populated."""
    waiter = cancellable_waiter  # Use short timeout
    dep_ready = rn("r_event")
    dep_failed_exc = rn("fx_event")
    dep_timeout = rn("t_event")

    status_r = StatusInfo(status=Status.READY)
    status_fx_detail = StatusInfo(status=Status.FAILED, error="FX_ERR_EV")

    async def watch_ready_dispatch(res_id: NamedResource) -> StatusInfo:
        if res_id == dep_ready:
            return status_r
        elif res_id == dep_failed_exc:
            raise ResourceFailedError(
                res_id.namespaced_name,
                "FX_ERR_EV",
            )
        elif res_id == dep_timeout:
            await asyncio.Future()  # Hang
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_dispatch)

    for dep in [dep_ready, dep_failed_exc, dep_timeout]:
        waiter.add(dep)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 3
    event_map = {e.resource_id: e for e in events}

    # Ready Event
    ev_ready = event_map[dep_ready]
    assert ev_ready.state == DependencyState.READY
    assert ev_ready.status_info == status_r
    assert ev_ready.error_message is None

    # Failed Event
    ev_failed = event_map[dep_failed_exc]
    assert ev_failed.state == DependencyState.FAILED
    assert ev_failed.error_message == "FX_ERR_EV"
    assert ev_failed.status_info == status_fx_detail

    # Timeout Event
    ev_timeout = event_map[dep_timeout]
    assert ev_timeout.state == DependencyState.TIMEOUT
    assert (
        "Timeout waiting for" in ev_timeout.error_message
        if ev_timeout.error_message
        else False
    )
    assert ev_timeout.status_info is None


async def test_unexpected_exception_in_watch_ready_becomes_failed_event(
    waiter: DependencyWaiter, mock_store: AsyncMock
) -> None:
    """Test that an unexpected error in store.watch_ready is caught and reported as FAILED."""
    dep_unexpected = rn("dep_unexp")
    error_message = "Something totally unexpected!"

    async def watch_ready_unexpected_error(res_id: NamedResource) -> StatusInfo:
        if res_id == dep_unexpected:
            raise ValueError(error_message)
        raise asyncio.InvalidStateError("Should not be called for other resources")

    mock_store.watch_ready = AsyncMock(side_effect=watch_ready_unexpected_error)
    waiter.add(dep_unexpected)

    events: list[DependencyResolutionEvent] = []
    async for event in waiter.watch():
        events.append(event)

    assert len(events) == 1
    event = events[0]
    assert event.resource_id == dep_unexpected
    assert event.state == DependencyState.FAILED
    assert (
        "Unexpected error: " + error_message in event.error_message
        if event.error_message
        else False
    )
    assert event.status_info is None

    summary = waiter.get_summary()
    assert summary.any_failed
    assert summary.failed_count == 1


async def test_summary_updates_correctly_as_events_arrive(
    waiter: DependencyWaiter, mock_store: AsyncMock
) -> None:
    """Test that get_summary() reflects the state accurately as events are processed."""
    dep1 = rn("s_dep1")
    dep2 = rn("s_dep2")
    dep3 = rn("s_dep3")

    s1 = StatusInfo(Status.READY)
    # dep2 will fail
    s3 = StatusInfo(Status.READY)

    # Control event processing order
    q1: asyncio.Queue[StatusInfo | Exception] = asyncio.Queue(1)
    q2: asyncio.Queue[StatusInfo | Exception] = asyncio.Queue(1)
    q3: asyncio.Queue[StatusInfo | Exception] = asyncio.Queue(1)

    async def controlled_watch_ready(
        res_id: NamedResource,
    ) -> StatusInfo:
        if res_id == dep1:
            queue = q1
        elif res_id == dep2:
            queue = q2
        elif res_id == dep3:
            queue = q3
        else:
            raise ValueError(f"Unexpected resource ID: {res_id}")
        val = await queue.get()
        if isinstance(val, Exception):
            raise val
        return val

    mock_store.watch_ready.side_effect = controlled_watch_ready

    waiter.add(dep1)
    waiter.add(dep2)
    waiter.add(dep3)

    await asyncio.sleep(0)  # Allow any immediate effects of add to settle if any

    # Initial summary: all pending
    summary0 = waiter.get_summary()
    assert summary0.pending_count == 3
    assert not summary0.all_ready
    assert not summary0.any_failed

    events_iter = waiter.watch()

    # Dep1 becomes ready
    await q1.put(s1)
    await asyncio.sleep(0)  # Ensure task runs and processes queue
    event1 = await events_iter.__anext__()
    assert event1.resource_id == dep1 and event1.state == DependencyState.READY
    summary1 = waiter.get_summary()
    assert summary1.ready_count == 1
    assert summary1.pending_count == 2
    assert not summary1.all_ready
    assert not summary1.any_failed

    # Dep2 fails
    await q2.put(ResourceFailedError(dep2.namespaced_name, "s2 failed"))
    await asyncio.sleep(0)  # Ensure task runs and processes queue
    event2 = await events_iter.__anext__()
    assert event2.resource_id == dep2 and event2.state == DependencyState.FAILED
    summary2 = waiter.get_summary()
    assert summary2.ready_count == 1
    assert summary2.failed_count == 1
    assert summary2.pending_count == 1
    assert not summary2.all_ready
    assert summary2.any_failed
    assert len(summary2.failed_dependencies) == 1

    # Dep3 becomes ready
    await q3.put(s3)
    await asyncio.sleep(0)  # Ensure task runs and processes queue
    event3 = await events_iter.__anext__()
    assert event3.resource_id == dep3 and event3.state == DependencyState.READY
    summary3 = waiter.get_summary()
    assert summary3.ready_count == 2
    assert summary3.failed_count == 1
    assert summary3.pending_count == 0
    assert not summary3.all_ready  # Because one failed
    assert summary3.any_failed

    # Ensure iterator is exhausted
    with pytest.raises(StopAsyncIteration):
        await events_iter.__anext__()

    # Final check on summary after iteration is complete
    summary_final = waiter.get_summary()
    assert summary_final.ready_count == 2
    assert summary_final.failed_count == 1
    assert summary_final.pending_count == 0
    assert not summary_final.all_ready
    assert summary_final.any_failed
