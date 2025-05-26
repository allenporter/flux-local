"""Task tracking service for Flux Local.

This service provides a simple way to track and wait for asynchronous tasks.
"""

import asyncio
from functools import partial
import logging
from typing import Any, Coroutine, Set
from abc import ABC, abstractmethod

_LOGGER = logging.getLogger(__name__)

__all__: list[str] = []


class TaskService(ABC):
    """Service for tracking and waiting for asynchronous tasks.

    This class handles the core task tracking functionality.
    """

    @abstractmethod
    def create_task(
        self, coro: Coroutine[None, None, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Create and track a new task.

        Args:
            coro: The coroutine to run as a task

        Returns:
            The created task
        """

    @abstractmethod
    def create_background_task(
        self, coro: Coroutine[None, None, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Create and track a new long running background task.

        Args:
            coro: The coroutine to run as a task

        Returns:
            The created task
        """

    @abstractmethod
    async def block_till_done(self) -> None:
        """Wait for all active non-background tasks to complete.

        This method creates a copy of the current active tasks and waits
        for them to complete. It's safe to call even if new tasks are created
        while waiting.
        """

    @abstractmethod
    async def wait_for_task(self, task: asyncio.Task[None]) -> None:
        """Wait for a specific task to complete.

        Args:
            task: The task to wait for
        """

    @abstractmethod
    def get_num_active_tasks(self) -> int:
        """Get the number of active non-background tasks."""


class TaskServiceImpl(TaskService):
    """Service for tracking and waiting for asynchronous tasks.

    This class handles the core task tracking functionality.
    """

    def __init__(self) -> None:
        """Initialize the task service."""
        self._active_tasks: Set[asyncio.Task[Any]] = set()
        self._background_tasks: Set[asyncio.Task[Any]] = set()

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
        task.add_done_callback(partial(self._task_done, self._active_tasks))
        return task

    def create_background_task(
        self, coro: Coroutine[None, None, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Create and track a new background task.

        Args:
            coro: The coroutine to run as a task

        Returns:
            The created task
        """
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(partial(self._task_done, self._background_tasks))
        return task

    def _task_done(
        self, task_set: Set[asyncio.Task[Any]], task: asyncio.Task[Any]
    ) -> None:
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
            task_set.discard(task)

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
            await asyncio.sleep(0)

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

    def get_num_active_tasks(self) -> int:
        """Get the number of active tasks."""
        return len(self._active_tasks)
