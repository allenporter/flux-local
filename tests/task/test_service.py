"""Tests for the TaskService."""

import asyncio
import logging
import pytest
from typing import Any

from flux_local.task.service import TaskService

_LOGGER = logging.getLogger(__name__)


@pytest.fixture
def task_service() -> TaskService:
    """Fixture for creating a TaskService instance."""
    return TaskService.get_instance()


async def test_create_and_complete_task(task_service: TaskService) -> None:
    """Test creating and completing a task."""

    async def test_task() -> Any:
        await asyncio.sleep(0.1)
        return "done"

    task = task_service.create_task(test_task())
    assert task in task_service._active_tasks

    result = await task
    assert result == "done"
    assert task not in task_service._active_tasks


async def test_block_till_done(task_service: TaskService) -> None:
    """Test blocking until all tasks are done."""

    async def test_task() -> Any:
        await asyncio.sleep(0.1)
        return "done"

    # Create multiple tasks
    tasks = [task_service.create_task(test_task()) for _ in range(3)]

    # Verify tasks are active
    assert len(task_service._active_tasks) == 3

    # Wait for all tasks to complete
    await task_service.block_till_done()

    # Verify all tasks are done and removed
    assert len(task_service._active_tasks) == 0
    for task in tasks:
        assert task.done()


async def test_task_failure(task_service: TaskService) -> None:
    """Test handling of task failures."""

    async def failing_task() -> Any:
        await asyncio.sleep(0.1)
        raise ValueError("Test error")

    task = task_service.create_task(failing_task())

    # Wait for the task to complete
    with pytest.raises(ValueError, match="Test error"):
        await task

    # Verify task is removed from active tasks
    assert task not in task_service._active_tasks


async def test_task_cancellation(task_service: TaskService) -> None:
    """Test task cancellation."""

    async def cancellable_task() -> Any:
        await asyncio.sleep(10)  # Should never complete
        return "should not get here"

    task = task_service.create_task(cancellable_task())

    # Cancel the task
    task.cancel()

    # Wait for the task to be cleaned up
    await asyncio.sleep(
        0.01
    )  # Give the event loop a chance to process the cancellation

    # Verify task is removed from active tasks
    assert task not in task_service._active_tasks
    assert task.cancelled()


async def test_wait_for_specific_task(task_service: TaskService) -> None:
    """Test waiting for a specific task."""

    async def test_task() -> Any:
        await asyncio.sleep(0.1)
        return "done"

    # Create multiple tasks
    tasks = [task_service.create_task(test_task()) for _ in range(3)]

    # Wait for a specific task
    target_task = tasks[1]
    await task_service.wait_for_task(target_task)

    # Verify the target task is done
    assert target_task.done()

    # Wait for all tasks to complete
    await task_service.block_till_done()

    # Verify all tasks are done
    assert tasks[0].done()
    assert tasks[2].done()


async def test_concurrent_tasks(task_service: TaskService) -> None:
    """Test handling of concurrent tasks."""

    async def test_task() -> Any:
        await asyncio.sleep(0.1)
        return "done"

    # Create tasks concurrently
    async def create_tasks() -> None:
        for _ in range(5):
            await asyncio.sleep(0.01)
            task_service.create_task(test_task())

    # Run multiple task creators concurrently
    await asyncio.gather(create_tasks(), create_tasks(), create_tasks())

    # Wait for all tasks to complete
    await task_service.block_till_done()

    # Verify all tasks are done and removed
    assert len(task_service._active_tasks) == 0


def test_singleton_behavior() -> None:
    """Test singleton behavior of TaskService."""
    # First instance
    service1 = TaskService.get_instance()
    assert isinstance(service1, TaskService)

    # Should get the same instance
    service2 = TaskService.get_instance()
    assert service1 is service2

    # Direct instantiation should raise
    with pytest.raises(RuntimeError):
        TaskService()


async def test_task_cleanup_after_cancellation(task_service: TaskService) -> None:
    """Test task cleanup when cancelled."""

    async def cancellable_task() -> Any:
        await asyncio.sleep(10)
        return "should not get here"

    task = task_service.create_task(cancellable_task())
    task.cancel()

    # Wait for cleanup
    await asyncio.sleep(0.01)

    # Verify task is cleaned up
    assert task not in task_service._active_tasks
    assert task.cancelled()

    # Verify task has a result set
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    else:
        assert False, "Expected CancelledError"


async def test_task_cleanup_in_wait_for_task(task_service: TaskService) -> None:
    """Test task cleanup when using wait_for_task."""

    async def test_task() -> Any:
        await asyncio.sleep(0.1)
        return "done"

    task = task_service.create_task(test_task())
    await task_service.wait_for_task(task)

    # Verify task is cleaned up
    assert task not in task_service._active_tasks
    assert task.done()
    assert task.result() == "done"


async def test_task_cleanup_in_block_till_done(task_service: TaskService) -> None:
    """Test task cleanup when using block_till_done."""

    async def test_task() -> Any:
        await asyncio.sleep(0.1)
        return "done"

    # Create multiple tasks
    tasks = [task_service.create_task(test_task()) for _ in range(3)]

    # Wait for all tasks to complete
    await task_service.block_till_done()

    # Verify all tasks are cleaned up
    assert len(task_service._active_tasks) == 0
    for task in tasks:
        assert task.done()
        assert task.result() == "done"


async def test_task_cleanup_with_exception(task_service: TaskService) -> None:
    """Test task cleanup when a task raises an exception."""

    async def failing_task() -> Any:
        await asyncio.sleep(0.1)
        raise ValueError("Test error")

    task = task_service.create_task(failing_task())

    # Wait for the task to fail
    with pytest.raises(ValueError, match="Test error"):
        await task_service.wait_for_task(task)

    # Verify task is cleaned up
    assert task not in task_service._active_tasks
    assert task.done()

    # Verify task has the correct exception
    with pytest.raises(ValueError, match="Test error"):
        task.result()
