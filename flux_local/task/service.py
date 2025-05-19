"""Task tracking service for Flux Local.

This service provides a simple way to track and wait for asynchronous tasks.
"""

import asyncio
import logging
from typing import Any, Coroutine, Set, Self

_LOGGER = logging.getLogger(__name__)


class TaskService:
    """Service for tracking and waiting for asynchronous tasks.

    This is implemented as a singleton to ensure consistent task tracking
    across all controllers.
    """

    _instance: Self | None = None

    @classmethod
    def get_instance(cls) -> Self:
        """Get the singleton instance of TaskService."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance  # type: ignore[no-any-return]

    def __init__(self) -> None:
        """Initialize the task service."""
        if self._instance is not None:
            raise RuntimeError(
                "TaskService is a singleton. Use get_instance() instead."
            )
        self._active_tasks: Set[asyncio.Task[Any]] = set()

    def create_task(
        self, coro: Coroutine[None, None, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Create and track a new task.

        Args:
            coro: The coroutine to run as a task

        Returns:
            The created task
        """
        task = asyncio.create_task(coro, name=name)
        self._active_tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        """Callback when a task is done.

        Args:
            task: The completed task
        """
        try:
            # This will raise any exception that occurred in the task
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _LOGGER.error("Task failed: %s", e)
        finally:
            # Remove task from active tasks
            self._active_tasks.discard(task)

    async def block_till_done(self) -> None:
        """Wait for all active tasks to complete.

        This method creates a copy of the current active tasks and waits
        for them to complete. It's safe to call even if new tasks are created
        while waiting.
        """
        active_tasks = list(self._active_tasks)
        if active_tasks:
            _LOGGER.debug("Waiting for %d tasks to complete", len(active_tasks))
            await asyncio.gather(*active_tasks)
        else:
            _LOGGER.debug("No active tasks to wait for")

    async def wait_for_task(self, task: asyncio.Task[None]) -> None:
        """Wait for a specific task to complete.

        Args:
            task: The task to wait for
        """
        if task not in self._active_tasks:
            raise ValueError("Task is not being tracked")

        # Wait for the task to complete
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Ensure the task is removed from active tasks
        self._active_tasks.discard(task)

        # If task was cancelled, ensure it's marked as done
        if task.cancelled():
            task.set_result(None)
