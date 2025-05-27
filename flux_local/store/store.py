"""Store module for holding state while visiting resources."""

from abc import ABC, abstractmethod
from collections.abc import Callable, AsyncGenerator
from enum import Enum
from typing import TypeVar, TYPE_CHECKING

from flux_local.manifest import BaseManifest, NamedResource

from .artifact import Artifact
from .status import Status, StatusInfo

T = TypeVar("T", bound=BaseManifest)
S = TypeVar("S", bound=Artifact)
U = TypeVar("U", bound=StatusInfo)
V = TypeVar("V", bound=BaseManifest | StatusInfo | Artifact)


SUPPORTS_STATUS: set[str] = set({
        "Kustomization",
        "GitRepository",
        "HelmRelease",
        "HelmRepository",
        "Bucket",
        "OCIRepository",
        "ImageRepository",
})


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
    def has_failed_resources(self) -> bool:
        """Check if any resources in the store have failed.

        Returns:
            bool: True if any resources have a failed status, False otherwise.
        """

    @abstractmethod
    def list_objects(self, kind: str | None = None) -> list[BaseManifest]:
        """List all manifest objects in the store, optionally filtered by kind."""

    @abstractmethod
    def add_listener(
        self,
        event: StoreEvent,
        callback: Callable[[NamedResource, V], None],
        flush: bool = False,
    ) -> Callable[[], None]:
        """Register a callback for a specific event (object added, status updated, artifact updated).

        Returns a callable that can be called to remove the listener.
        """

    @abstractmethod
    async def watch_ready(self, resource_id: NamedResource) -> StatusInfo:
        """
        Wait for the specified resource to become READY.

        If the resource is already READY, returns its StatusInfo immediately.
        If the resource is FAILED or transitions to FAILED, raises ResourceFailedError.
        Handles asyncio.CancelledError.

        Args:
            resource_id: The NamedResource to watch.

        Returns:
            StatusInfo of the resource when it becomes READY.

        Raises:
            ResourceFailedError: If the resource is or becomes FAILED.
            asyncio.CancelledError: If the watch is cancelled.
        """

    @abstractmethod
    async def watch_added(
        self, kind: str
    ) -> AsyncGenerator[tuple[NamedResource, BaseManifest]]:
        """
        Watch for new objects of a specific kind being added to the store.

        This is an asynchronous iterator that yields tuples of (NamedResource, BaseManifest)
        as objects of the specified kind are added.

        Args:
            kind: The kind of resource to watch for (e.g., "Kustomization", "GitRepository").

        Yields:
            A tuple containing the NamedResource identifier and the BaseManifest object
            when a new object of the specified kind is added.
        """
        if TYPE_CHECKING:
            yield None, None  # type: ignore[misc]

    @abstractmethod
    async def watch_exists(self, resource_id: NamedResource) -> BaseManifest:
        """
        Wait for the specified resource to exist in the store.

        If the resource already exists, returns its BaseManifest immediately.
        Handles asyncio.CancelledError. The caller is expected to handle timeouts.

        Args:
            resource_id: The NamedResource to watch for existence.

        Returns:
            BaseManifest of the resource when it is added to the store.

        Raises:
            asyncio.CancelledError: If the watch is cancelled.
            ObjectNotFoundError: If the watch completes but the object is still not found (should not happen with correct event logic).
        """
