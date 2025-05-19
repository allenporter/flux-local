"""Task tracking module for Flux Local.

This module provides a simple task tracking service that allows
controllers to track and wait for asynchronous tasks.
"""

from .service import TaskService

__all__ = ["TaskService"]
