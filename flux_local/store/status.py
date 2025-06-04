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

    def __str__(self) -> str:
        """Return a string representation of the status."""
        if self.error:
            return f"{self.status}: {self.error}"
        return str(self.status)
