"""Status information for a resource."""

from enum import StrEnum
from dataclasses import dataclass


class Status(StrEnum):
    """Processing status for a resource."""

    PENDING = "Pending"
    READY = "Ready"
    FAILED = "Failed"


@dataclass
class StatusInfo:
    """Processing status and optional error message for a resource."""

    status: Status
    error: str | None = None
