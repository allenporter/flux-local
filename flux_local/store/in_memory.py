"""Module for in memory object store."""

import dataclasses
import asyncio
from collections import defaultdict
from collections.abc import Callable, AsyncGenerator
from typing import Any, TypeVar, DefaultDict

import logging

from flux_local.manifest import BaseManifest, NamedResource
from flux_local.exceptions import ResourceFailedError

from .artifact import Artifact
from .status import Status, StatusInfo
from .store import Store, StoreEvent


_LOGGER = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseManifest)
S = TypeVar("S", bound=Artifact)
U = TypeVar("U", bound=StatusInfo)
V = TypeVar("V", bound=BaseManifest | StatusInfo | Artifact)


class InMemoryStore(Store):
    """In-memory implementation of the Store interface.
    Stores manifest objects, status, and artifacts keyed by NamedResource.
    Supports event listeners for object, status, and artifact changes.
    """

    def __init__(self) -> None:
        """Initialize the InMemoryStore."""
        self._objects: dict[NamedResource, BaseManifest] = {}
        self._status: dict[NamedResource, StatusInfo] = {}
        self._artifacts: dict[NamedResource, Artifact] = {}
        self._listeners: DefaultDict[StoreEvent, list[Callable[..., None]]] = (
            defaultdict(list)
        )

    def add_object(self, obj: T) -> None:
        """Add a manifest object to the store."""
        if (
            not hasattr(obj, "kind")
            or not hasattr(obj, "namespace")
            or not hasattr(obj, "name")
        ):
            raise ValueError("Object must have kind, namespace, and name attributes")
        _LOGGER.debug("Adding object %s to store", obj)
        resource_id = NamedResource(obj.kind, getattr(obj, "namespace", None), obj.name)
        if (existing := self._objects.get(resource_id)) is not None:
            if dataclasses.asdict(existing) == dataclasses.asdict(obj):
                _LOGGER.debug(
                    "Object %s already exists in store, skipping", resource_id
                )
                return
            _LOGGER.debug("Updating existing object %s in store", resource_id)

        self._objects[resource_id] = obj
        self._fire_event(StoreEvent.OBJECT_ADDED, resource_id, obj)

    def get_object(self, resource_id: NamedResource, cls: type[T]) -> T | None:
        """Retrieve a manifest object by resource identity and type."""
        obj = self._objects.get(resource_id)
        if obj is not None:
            if isinstance(obj, cls):
                return obj
            raise ValueError(
                f"Object {resource_id.namespaced_name} is not of type {cls.__name__} (was {obj.__class__.__name__})"
            )
        return None

    def update_status(
        self, resource_id: NamedResource, status: Status, error: str | None = None
    ) -> None:
        """Update the processing status and optional error message for a resource."""
        if status == Status.FAILED:
            _LOGGER.error(
                "Resource %s status %s with error: %s",
                resource_id.namespaced_name,
                status,
                error,
            )
        else:
            _LOGGER.debug(
                "Updating status for resource %s to %s (%s)",
                resource_id.namespaced_name,
                status,
                error,
            )
        self._status[resource_id] = StatusInfo(status=status, error=error)
        self._fire_event(
            StoreEvent.STATUS_UPDATED, resource_id, self._status[resource_id]
        )

    def get_status(self, resource_id: NamedResource) -> StatusInfo | None:
        """Retrieve the processing status for a resource."""
        status_info = self._status.get(resource_id)
        if status_info:
            return status_info
        return None

    def set_artifact(self, resource_id: NamedResource, artifact: S) -> None:
        """Store artifact information (e.g., source path, revision, rendered manifests) for a resource."""
        if not isinstance(artifact, Artifact):
            raise ValueError(
                f"Artifact/set {resource_id.namespaced_name} is not of type {Artifact.__name__} (was {artifact.__class__.__name__})"
            )
        self._artifacts[resource_id] = artifact
        self._fire_event(StoreEvent.ARTIFACT_UPDATED, resource_id, artifact)

    def get_artifact(self, resource_id: NamedResource, cls: type[S]) -> S | None:
        """Retrieve artifact information for a resource."""
        artifact = self._artifacts.get(resource_id)
        if artifact is not None:
            if not isinstance(artifact, cls):
                raise ValueError(
                    f"Artifact/get {resource_id.namespaced_name} is not of type {cls.__name__} (was {artifact.__class__.__name__})"
                )
            return artifact
        return None

    def has_failed_resources(self) -> bool:
        """Check if any resources in the store have failed.

        Returns:
            bool: True if any resources have a failed status, False otherwise.
        """
        for status_info in self._status.values():
            if status_info.status == Status.FAILED:
                return True
        return False

    def list_objects(self, kind: str | None = None) -> list[BaseManifest]:
        """List all manifest objects in the store, optionally filtered by kind."""
        if kind is None:
            return list(self._objects.values())
        return [
            obj for obj in self._objects.values() if getattr(obj, "kind", None) == kind
        ]

    def add_listener(
        self,
        event: StoreEvent,
        callback: Callable[[NamedResource, V], None],
        flush: bool = False,
    ) -> Callable[[], None]:
        """Register a callback for a specific event (object added, status updated, artifact updated)."""

        def remove() -> None:
            if callback in self._listeners[event]:
                self._listeners[event].remove(callback)

        self._listeners[event].append(callback)

        if flush:
            _LOGGER.debug("Flushing objects for event type %s", event)
            for obj in list(self.list_objects()):
                if (
                    not hasattr(obj, "kind")
                    or not hasattr(obj, "namespace")
                    or not hasattr(obj, "name")
                ):
                    _LOGGER.warning("Object %s is missing required attributes", obj)
                    continue
                rid = NamedResource(obj.kind, obj.namespace, obj.name)
                if event == StoreEvent.OBJECT_ADDED:
                    callback(rid, obj)  # type: ignore
                elif event == StoreEvent.STATUS_UPDATED:
                    if status := self._status.get(rid):
                        callback(rid, status)  # type: ignore
                elif event == StoreEvent.ARTIFACT_UPDATED:
                    if artifact := self._artifacts.get(rid):
                        callback(rid, artifact)  # type: ignore

        return remove

    def _fire_event(self, event: StoreEvent, *args: Any) -> None:
        for cb in list(self._listeners[event]):  # Iterate over a copy for safe removal
            try:
                cb(*args)
            except Exception:
                _LOGGER.exception("Store listener callback failed for event %s", event)

    async def watch_ready(self, resource_id: NamedResource) -> StatusInfo:
        """
        Wait for the specified resource to become READY.

        If the resource is already READY, returns its StatusInfo immediately.
        If the resource is FAILED or transitions to FAILED, raises ResourceFailedError.
        Handles asyncio.CancelledError.
        """
        current_status_info = self.get_status(resource_id)
        if current_status_info:
            if current_status_info.status == Status.READY:
                return current_status_info
            if current_status_info.status == Status.FAILED:
                raise ResourceFailedError(
                    resource_id.namespaced_name,
                    current_status_info.error or "Resource is FAILED",
                )

        event_fired = asyncio.Event()
        # Using a list to pass StatusInfo or Exception from callback
        result_holder: list[StatusInfo | Exception] = []

        def callback(fired_resource_id: NamedResource, status_info: StatusInfo) -> None:
            if fired_resource_id == resource_id:
                if status_info.status == Status.READY:
                    result_holder.append(status_info)
                    event_fired.set()
                elif status_info.status == Status.FAILED:
                    result_holder.append(
                        ResourceFailedError(
                            resource_id.namespaced_name,
                            status_info.error or "Resource transitioned to FAILED",
                        )
                    )
                    event_fired.set()

        remove_listener = self.add_listener(StoreEvent.STATUS_UPDATED, callback)

        try:
            await event_fired.wait()
            if result_holder:
                result = result_holder[0]
                if isinstance(result, ResourceFailedError):
                    raise result
                if isinstance(result, StatusInfo) and result.status == Status.READY:
                    return result
            # Should not be reached if event_fired was set correctly by callback
            # but as a safeguard, re-check status if wait completes unexpectedly.
            final_status = self.get_status(resource_id)
            if final_status and final_status.status == Status.READY:
                return final_status
            raise RuntimeError(
                f"watch_ready for {resource_id} ended unexpectedly without resolution."
            )
        except asyncio.CancelledError:
            _LOGGER.debug("watch_ready for %s cancelled.", resource_id)
            raise
        finally:
            remove_listener()

    async def watch_added(
        self, kind: str
    ) -> AsyncGenerator[tuple[NamedResource, BaseManifest]]:
        """
        Watch for new objects of a specific kind being added to the store.

        This is an asynchronous iterator that yields tuples of (NamedResource, BaseManifest)
        as objects of the specified kind are added. It will first yield any existing
        objects of the specified kind already in the store.

        Args:
            kind: The kind of resource to watch for (e.g., "Kustomization", "GitRepository").

        Yields:
            A tuple containing the NamedResource identifier and the BaseManifest object
            when a new object of the specified kind is added.
        """
        # First, yield existing objects of the specified kind
        for resource_id, obj in list(self._objects.items()):  # Iterate over a copy
            if hasattr(obj, "kind") and obj.kind == kind:
                yield resource_id, obj

        # Then, listen for new objects
        queue: asyncio.Queue[tuple[NamedResource, BaseManifest]] = asyncio.Queue()

        def callback(added_resource_id: NamedResource, added_obj: BaseManifest) -> None:
            if hasattr(added_obj, "kind") and added_obj.kind == kind:
                try:
                    queue.put_nowait((added_resource_id, added_obj))
                except asyncio.QueueFull:
                    _LOGGER.warning(
                        "watch_added queue full for kind %s. This might indicate a runaway producer or slow consumer.",
                        kind,
                    )

        remove_listener = self.add_listener(StoreEvent.OBJECT_ADDED, callback)

        try:
            while True:
                # Wait for the next item from the queue
                # This can be cancelled if the generator is closed
                resource_id, obj = await queue.get()
                yield resource_id, obj
                queue.task_done()  # Signal that the item has been processed
        except asyncio.CancelledError:
            _LOGGER.debug("watch_added for kind '%s' cancelled.", kind)
            # Propagate cancellation to allow cleanup
            raise
        finally:
            _LOGGER.debug("Cleaning up listener for watch_added (kind: %s)", kind)
            remove_listener()
