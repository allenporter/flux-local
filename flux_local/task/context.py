"""Context management for TaskService."""

import contextvars
import contextlib
import logging
from collections.abc import Generator

from .service import TaskService, TaskServiceImpl

__all__: list[str] = []

_LOGGER = logging.getLogger(__name__)

# Context variable for the current task service instance
_task_service_ctx: contextvars.ContextVar[TaskService | None] = contextvars.ContextVar(
    "_task_service_ctx", default=None
)


def get_task_service() -> TaskService:
    """Get the current task service instance.

    If no instance is set in the context variable, creates a new one.
    """
    instance = _task_service_ctx.get()
    if instance is None:
        instance = TaskServiceImpl()
        _task_service_ctx.set(instance)
    return instance


@contextlib.contextmanager
def task_service_context(
    service: TaskService | None = None,
) -> Generator[TaskService, None, None]:
    """Context manager for managing TaskService instances.

    This generator handles the creation and cleanup of TaskService instances
    within a specific context.

    Args:
        service: Optional existing TaskService instance to use. If None,
                 a new instance will be created.

    Yields:
        The TaskService instance to use within the context
    """
    service = service or TaskServiceImpl()
    _task_service_ctx.set(service)
    try:
        yield service
    finally:
        _task_service_ctx.set(None)
