"""
Provides a utility for waiting on multiple dependencies to become ready or fail.
"""

import asyncio
from collections.abc import AsyncGenerator
import logging
from dataclasses import dataclass, field
from enum import Enum

from flux_local.manifest import NamedResource
from flux_local.exceptions import ResourceFailedError
from flux_local.task import TaskService

from .store import Store, SUPPORTS_STATUS
from .status import Status, StatusInfo

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60  # Default timeout for individual dependencies


class DependencyState(Enum):
    """Represents the state of a dependency being watched."""

    PENDING = "Pending"
    READY = "Ready"
    FAILED = "Failed"
    TIMEOUT = "Timeout"
    CANCELLED = "Cancelled"


@dataclass
class DependencyResolutionEvent:
    """Event representing the resolution of a single dependency."""

    resource_id: NamedResource
    state: DependencyState
    status_info: StatusInfo | None = None
    error_message: str | None = None

    @property
    def success(self) -> bool:
        """Return True if the dependency resolved successfully."""
        return self.state == DependencyState.READY

    @property
    def failure(self) -> bool:
        """Return True if the dependency failed or timed out."""
        return self.state in [DependencyState.FAILED, DependencyState.TIMEOUT]


@dataclass
class DependencySummary:
    """Summary of the state of all dependencies."""

    parent_resource_id: NamedResource
    all_ready: bool = False
    any_failed: bool = False  # True if any dependency is FAILED, TIMEOUT, or CANCELLED
    total_dependencies: int = 0
    pending_count: int = 0
    ready_count: int = 0
    failed_count: int = 0  # Consolidated count for FAILED, TIMEOUT, CANCELLED states
    pending_dependencies: list[NamedResource] = field(default_factory=list)
    ready_dependencies: list[DependencyResolutionEvent] = field(default_factory=list)
    # This list will contain events for FAILED, TIMEOUT, and CANCELLED states
    failed_dependencies: list[DependencyResolutionEvent] = field(default_factory=list)

    @property
    def summary_message(self) -> str:
        """Return a human-readable summary of the dependency states."""
        if self.any_failed:
            failed_deps = [
                f"{dep.resource_id.namespaced_name}: {dep.state}"
                for dep in self.failed_dependencies
            ]
            return f"Failed dependencies: {failed_deps}"
        if self.pending_count > 0:
            pending_deps = [dep.namespaced_name for dep in self.pending_dependencies]
            return f"Pending dependencies: {pending_deps}"
        if self.all_ready:
            return "All dependencies ready."
        # Default summary if no specific state is met
        return (
            f"Dependency Summary for {self.parent_resource_id.namespaced_name}:\n"
            f"  Total: {self.total_dependencies}\n"
            f"  Ready: {self.ready_count}\n"
            f"  Pending: {self.pending_count}\n"
            f"  Failed/Timed Out/Cancelled: {self.failed_count}\n"
        )


class DependencyWaiter:
    """
    Manages watching multiple dependencies (NamedResource) for readiness or failure
    using a Store.
    """

    def __init__(
        self,
        store: Store,
        task_service: TaskService,
        parent_resource_id: NamedResource,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        """
        Initialize the DependencyWaiter.

        Args:
            store: The Store instance to use for watching resources.
            task_service: The TaskService for creating background tasks.
            parent_resource_id: The resource that is waiting for these dependencies.
                                Used for logging and status updates.
            timeout_seconds: Timeout for each individual dependency watch.
        """
        self._store = store
        self._task_service = task_service
        self._parent_resource_id = parent_resource_id
        self._timeout_seconds = timeout_seconds
        self._dependencies: dict[NamedResource, asyncio.Task[StatusInfo]] = {}
        self._resolutions: dict[NamedResource, DependencyResolutionEvent] = {}
        self._watch_task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[DependencyResolutionEvent] = asyncio.Queue()
        self._current_summary = DependencySummary(parent_resource_id=parent_resource_id)

    def add(self, resource_id: NamedResource) -> None:
        """
        Add a resource to watch.

        If the waiter has already started watching, this will raise an error.
        """
        if self._watch_task is not None:
            raise ValueError("Cannot add dependencies after watching has started.")
        if resource_id in self._dependencies:
            raise ValueError(f"Dependency {resource_id.namespaced_name} already added.")
        _LOGGER.debug(
            "Adding dependency %s for %s", resource_id, self._parent_resource_id
        )
        # The task will be created when `watch` is called.
        # Store a placeholder or simply the ID for now.
        self._dependencies[resource_id] = None  # type: ignore[assignment]

    async def _watch_single_dependency(self, resource_id: NamedResource) -> None:
        """Internal task to watch a single dependency and put its resolution on the queue."""
        try:
            _LOGGER.debug(
                "Parent %s starting watch for dependency: %s (kind: %s)",
                self._parent_resource_id.namespaced_name,
                resource_id.namespaced_name,
                resource_id.kind,
            )
            async with asyncio.timeout(self._timeout_seconds):
                if resource_id.kind not in SUPPORTS_STATUS:
                    _LOGGER.debug(
                        "Using watch_exists for %s %s",
                        resource_id.kind,
                        resource_id.namespaced_name,
                    )
                    # For ConfigMap and Secret, existence implies readiness.
                    # watch_exists returns the manifest, but we don't need it here,
                    # just the fact that it returned successfully.
                    await self._store.watch_exists(resource_id)
                    # Create a synthetic StatusInfo indicating readiness.
                    status_info = StatusInfo(status=Status.READY)
                else:
                    _LOGGER.debug(
                        "Using watch_ready for %s %s",
                        resource_id.kind,
                        resource_id.namespaced_name,
                    )
                    status_info = await self._store.watch_ready(resource_id)

            _LOGGER.debug(
                "Dependency %s for %s resolved as %s.",
                resource_id.namespaced_name,
                self._parent_resource_id.namespaced_name,
                status_info.status,
            )
            event = DependencyResolutionEvent(
                resource_id=resource_id,
                state=DependencyState.READY,
                status_info=status_info,
            )
        except ResourceFailedError as e:
            _LOGGER.warning(
                "Dependency %s for %s FAILED: %s",
                resource_id.namespaced_name,
                self._parent_resource_id.namespaced_name,
                e.message,
            )
            event = DependencyResolutionEvent(
                resource_id=resource_id,
                state=DependencyState.FAILED,
                error_message=e.message,
                status_info=StatusInfo(status=Status.FAILED, error=e.message),
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Dependency %s for %s TIMEOUT after %s seconds.",
                resource_id.namespaced_name,
                self._parent_resource_id.namespaced_name,
                self._timeout_seconds,
            )
            event = DependencyResolutionEvent(
                resource_id=resource_id,
                state=DependencyState.TIMEOUT,
                error_message=f"Timeout waiting for {resource_id.namespaced_name}",
            )
        except asyncio.CancelledError:
            _LOGGER.info(
                "Watch for dependency %s for %s was CANCELLED.",
                resource_id.namespaced_name,
                self._parent_resource_id.namespaced_name,
            )
            event = DependencyResolutionEvent(
                resource_id=resource_id, state=DependencyState.CANCELLED
            )
            # Do not re-raise CancelledError here, let the main watch loop handle it
        except Exception as e:
            _LOGGER.error(
                "Unexpected error watching dependency %s for %s: %s",
                resource_id.namespaced_name,
                self._parent_resource_id.namespaced_name,
                e,
                exc_info=True,
            )
            event = DependencyResolutionEvent(
                resource_id=resource_id,
                state=DependencyState.FAILED,  # Treat unexpected as FAILED
                error_message=f"Unexpected error: {str(e)}",
            )

        self._resolutions[resource_id] = event
        await self._event_queue.put(event)
        # Remove from active dependencies task map
        if resource_id in self._dependencies:
            del self._dependencies[resource_id]

    async def watch(self) -> AsyncGenerator[DependencyResolutionEvent, None]:
        """
        Starts watching all added dependencies and yields events as they resolve.

        This method will launch tasks for each dependency and then yield
        DependencyResolutionEvent objects as each dependency becomes ready, fails,
        or times out.

        It completes when all dependencies have resolved or when `cancel_pending_watches`
        is called and all active watches are terminated.
        """
        if not self._dependencies:
            _LOGGER.debug("No dependencies to watch for %s.", self._parent_resource_id)
            return

        if self._watch_task is not None:
            raise RuntimeError("Watcher already started.")

        # Create tasks for all dependencies added so far
        for dep_id in list(self._dependencies.keys()):
            if self._dependencies[dep_id] is None:
                self._dependencies[dep_id] = self._task_service.create_task(
                    self._watch_single_dependency(dep_id)
                )

        active_watches = len(self._dependencies)
        resolved_count = 0
        _LOGGER.debug(
            "Starting watch for %d dependencies for %s.",
            active_watches,
            self._parent_resource_id.namespaced_name,
        )

        try:
            while resolved_count < active_watches:
                if self._event_queue.empty() and not self._dependencies:
                    # This can happen if tasks complete very quickly or are cancelled
                    # before the loop gets to yield from queue.
                    break
                try:
                    # Wait for the next event from the queue
                    event = await self._event_queue.get()
                    yield event
                    resolved_count += 1
                    if event.resource_id in self._dependencies:
                        _LOGGER.warning(
                            "Resolved dependency %s still in active task list.",
                            event.resource_id,
                        )
                        del self._dependencies[event.resource_id]

                except asyncio.CancelledError:
                    _LOGGER.info(
                        "DependencyWaiter.watch for %s cancelled. Cleaning up.",
                        self._parent_resource_id,
                    )
                    await self.cancel_pending_watches()  # Ensure all sub-tasks are cancelled
                    # Yield any remaining events that might have been queued before cancellation
                    while not self._event_queue.empty():
                        try:
                            event = self._event_queue.get_nowait()
                            yield event
                        except asyncio.QueueEmpty:
                            break
                    raise  # Re-raise CancelledError
        finally:
            _LOGGER.debug(
                "Exiting watch loop for %s. Resolved: %d, Expected: %d",
                self._parent_resource_id,
                resolved_count,
                active_watches,
            )
            # Ensure all tasks are truly finished or cancelled
            await self.cancel_pending_watches()
            self._watch_task = None  # Mark as stopped

    def get_summary(self) -> DependencySummary:
        """Return a summary of the current state of all dependencies."""
        # Always recalculate for simplicity and correctness.

        ready_count = 0
        consolidated_failed_count = 0

        ready_events_list: list[DependencyResolutionEvent] = []
        # This list will store events for FAILED, TIMEOUT, and CANCELLED states
        failed_events_list: list[DependencyResolutionEvent] = []

        # Process resolved dependencies from self._resolutions
        for dep_id, resolution_event in self._resolutions.items():
            if resolution_event.state == DependencyState.READY:
                ready_count += 1
                ready_events_list.append(resolution_event)
            elif resolution_event.state in (
                DependencyState.FAILED,
                DependencyState.TIMEOUT,
                DependencyState.CANCELLED,
            ):
                consolidated_failed_count += 1
                failed_events_list.append(resolution_event)
            elif resolution_event.state == DependencyState.PENDING:
                # This case should ideally not happen if _resolutions only stores final states.
                _LOGGER.warning(
                    "Resolution for %s (parent: %s) unexpectedly found in PENDING state in _resolutions. Counting as failed.",
                    resolution_event.resource_id.namespaced_name,
                    self._parent_resource_id.namespaced_name,
                )
                consolidated_failed_count += 1
                # Add a generic event or the original if it makes sense
                failed_events_list.append(
                    DependencyResolutionEvent(
                        resource_id=dep_id,
                        state=DependencyState.FAILED,
                        error_message="Pending in resolutions",
                    )
                )
            else:  # Should not happen with current Enum
                _LOGGER.error(
                    "Resolution for %s (parent: %s) has an unknown state: %s in _resolutions.",
                    resolution_event.resource_id.namespaced_name,
                    self._parent_resource_id.namespaced_name,
                    resolution_event.state,
                )
                consolidated_failed_count += 1
                failed_events_list.append(
                    DependencyResolutionEvent(
                        resource_id=dep_id,
                        state=DependencyState.FAILED,
                        error_message="Unknown state in resolution",
                    )
                )

        # Process dependencies that are still being actively watched or are queued (in self._dependencies)
        # These are considered PENDING.
        pending_deps_resources: list[NamedResource] = []
        for dep_id_pending in self._dependencies.keys():
            # A dependency in self._dependencies is pending if it's not yet in self._resolutions.
            # Tasks in self._dependencies are removed once they complete and populate self._resolutions.
            pending_deps_resources.append(dep_id_pending)

        pending_count = len(pending_deps_resources)

        # Total dependencies is the sum of all distinct outcomes (ready, failed/timedout/cancelled, pending)
        total_dependencies = ready_count + consolidated_failed_count + pending_count

        all_ready_flag = total_dependencies > 0 and ready_count == total_dependencies
        # any_failed is true if there's at least one event in failed_events_list
        any_failed_flag = bool(failed_events_list)

        return DependencySummary(
            parent_resource_id=self._parent_resource_id,
            all_ready=all_ready_flag,
            any_failed=any_failed_flag,
            total_dependencies=total_dependencies,
            ready_count=ready_count,
            pending_count=pending_count,
            failed_count=consolidated_failed_count,
            pending_dependencies=pending_deps_resources,
            ready_dependencies=ready_events_list,
            failed_dependencies=failed_events_list,
        )

    async def cancel_pending_watches(self) -> None:
        """
        Cancels all currently active (pending) dependency watch tasks.
        Also marks dependencies that were added but never started (task is None) as CANCELLED.
        """
        _LOGGER.debug(
            "Cancelling pending watches for %s. Dependencies count: %d",
            self._parent_resource_id,
            len(self._dependencies),
        )
        cancelled_tasks = []
        # Iterate over a copy of the items, as self._dependencies might be modified
        # by _watch_single_dependency if a task completes/cancels during this loop.
        for dep_id, task in list(self._dependencies.items()):
            _LOGGER.debug(
                "Processing cancellation for dependency %s (parent: %s), task: %s",
                dep_id.namespaced_name,
                self._parent_resource_id.namespaced_name,
                "Exists" if task else "None",
            )
            if not task.done():
                _LOGGER.debug(
                    "Actively cancelling task for dependency: %s for %s",
                    dep_id,
                    self._parent_resource_id,
                )
                task.cancel()
                cancelled_tasks.append(task)
                # The task's CancelledError handler in _watch_single_dependency
                # will update self._resolutions and remove from self._dependencies.
            else:
                _LOGGER.debug(
                    "Dependency %s for %s task already done. Ensuring it's removed from active list.",
                    dep_id,
                    self._parent_resource_id,
                )
                # _watch_single_dependency should have handled _resolutions and _dependencies removal.
                # If it's still in _dependencies, remove it.
                if dep_id in self._dependencies:
                    del self._dependencies[dep_id]

        if cancelled_tasks:
            try:
                _LOGGER.debug(
                    "Awaiting cancellation of %d tasks for %s.",
                    len(cancelled_tasks),
                    self._parent_resource_id.namespaced_name,
                )
                await asyncio.gather(*cancelled_tasks, return_exceptions=True)
                _LOGGER.debug(
                    "Finished awaiting task cancellations for %s.",
                    self._parent_resource_id.namespaced_name,
                )
            except RuntimeError as e:
                _LOGGER.warning(
                    "RuntimeError during asyncio.gather for task cancellation for %s: %s. Tasks might not be fully cancelled.",
                    self._parent_resource_id.namespaced_name,
                    e,
                )

        # Process all dependencies that were originally in _dependencies.
        # Some might have been removed by their own _watch_single_dependency task if they were cancelled.
        # Others might have had task=None (never started).
        # Iterate over a copy of keys, as we will modify _dependencies.
        for dep_id in list(self._dependencies.keys()):
            if dep_id not in self._resolutions:
                # This dependency has not been resolved yet (e.g., READY, FAILED, TIMEOUT,
                # or already CANCELLED by its own task processing CancelledError).
                # This path primarily handles dependencies where task was None, or where
                # an active task's cancellation didn't lead to it updating _resolutions.
                _LOGGER.debug(
                    "Dependency %s (for %s) not in resolutions after active task cancellation phase. Marking as CANCELLED.",
                    dep_id.namespaced_name,
                    self._parent_resource_id.namespaced_name,
                )
                event = DependencyResolutionEvent(
                    resource_id=dep_id, state=DependencyState.CANCELLED
                )
                self._resolutions[dep_id] = event
                # No need to put on _event_queue, as the watch() loop is likely terminating or already terminated.

            # Remove from _dependencies as it's now considered handled by the cancellation process
            # (either resolved by its task, or explicitly marked CANCELLED above).
            # This ensures _dependencies only contains truly pending items if watch were to continue.
            del self._dependencies[dep_id]

        _LOGGER.debug(
            "Finished cancelling pending watches for %s. Final dependencies count: %d. Resolutions count: %d.",
            self._parent_resource_id.namespaced_name,
            len(self._dependencies),  # Should be 0 after this method completes
            len(self._resolutions),
        )
