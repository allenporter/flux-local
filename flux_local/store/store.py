"""Store module for holding state while visiting resources."""

from abc import ABC, abstractmethod
from typing import TypeVar, Callable
from enum import Enum

from flux_local.manifest import NamedResource, BaseManifest
from .artifact import Artifact
from .status import Status, StatusInfo

T = TypeVar("T", bound=BaseManifest)
S = TypeVar("S", bound=Artifact)
U = TypeVar("U", bound=StatusInfo)
V = TypeVar("V", bound=BaseManifest | StatusInfo | Artifact)


class StoreEvent(str, Enum):
    """Enum for store events."""

    OBJECT_ADDED = "object_added"
    STATUS_UPDATED = "status_updated"
    ARTIFACT_UPDATED = "artifact_updated"


class Store(ABC):
    """Abstract base class for the central object type-safe object store with listener support."""

    @abstractmethod
    def add_object(self, obj: T) -> None:
        """Add a manifest object to the store."""

    @abstractmethod
    def get_object(self, resource_id: NamedResource, cls: type[T]) -> T | None:
        """Retrieve a manifest object by resource identity and type."""

    @abstractmethod
    def update_status(
        self, resource_id: NamedResource, status: Status, error: str | None = None
    ) -> None:
        """Update the processing status and optional error message for a resource."""

    @abstractmethod
    def get_status(self, resource_id: NamedResource) -> StatusInfo | None:
        """Retrieve the processing status for a resource."""

    @abstractmethod
    def set_artifact(self, resource_id: NamedResource, artifact: S) -> None:
        """Store artifact information (e.g., source path, revision, rendered manifests) for a resource."""

    @abstractmethod
    def get_artifact(self, resource_id: NamedResource, cls: type[S]) -> S | None:
        """Retrieve artifact information for a resource."""

    @abstractmethod
    def list_objects(self, kind: str | None = None) -> list[BaseManifest]:
        """List all manifest objects in the store, optionally filtered by kind."""

    @abstractmethod
    def add_listener(
        self,
        event: StoreEvent,
        callback: Callable[[NamedResource, V], None],
    ) -> Callable[[], None]:
        """Register a callback for a specific event (object added, status updated, artifact updated).

        Returns a callable that can be called to remove the listener.
        """
