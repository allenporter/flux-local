"""Module for in memory object store."""

import dataclasses
import asyncio
from collections import defaultdict
from collections.abc import Callable, AsyncGenerator
from typing import Any, TypeVar, DefaultDict

import logging

from flux_local.manifest import BaseManifest, NamedResource
from flux_local.exceptions import ResourceFailedError, ObjectNotFoundError

from .artifact import Artifact
from .status import Status, StatusInfo
from .store import Store, StoreEvent, SUPPORTS_STATUS


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
        self._existence_waiters: DefaultDict[NamedResource, asyncio.Event] = (
            defaultdict(asyncio.Event)
        )
        self._existence_waiter_locks: DefaultDict[NamedResource, asyncio.Lock] = (
            defaultdict(asyncio.Lock)
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

        # Notify any watchers waiting for this specific object's existence
        if resource_id in self._existence_waiters:
            self._existence_waiters[resource_id].set()
            # The event is one-time, remove it after setting
            # Consider if removal here is always correct or if event should persist
            # For watch_exists, once it's set, the current waiters are done.
            # New waiters will create a new event or find the object already present.
            # However, to prevent old events from being reused if not cleaned up by waiter,
            # it's safer to clear it. The waiter should also clean up.
            # Let's have the waiter clean up its specific event.
            # For now, just set. The waiter will handle its own cleanup.

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
        if resource_id.kind not in SUPPORTS_STATUS:
            raise ValueError(
                f"Resource kind {resource_id.kind} does not support status updates"
            )
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

    async def watch_exists(self, resource_id: NamedResource) -> BaseManifest:
        """
        Wait for the specified resource to exist in the store.

        If the resource already exists, returns its BaseManifest immediately.
        Handles asyncio.CancelledError.
        """
        # Check if object already exists
        if (obj := self._objects.get(resource_id)) is not None:
            _LOGGER.debug("watch_exists: Object %s already in store.", resource_id)
            return obj

        # Lock to prevent race conditions when multiple coroutines check/create the event
        async with self._existence_waiter_locks[resource_id]:
            # Re-check after acquiring lock, in case it was added while waiting for the lock
            if (obj := self._objects.get(resource_id)) is not None:
                _LOGGER.debug(
                    "watch_exists: Object %s found in store after acquiring lock.",
                    resource_id,
                )
                # Clean up lock if no longer needed (optional, defaultdict handles creation)
                # If we are sure this is the only waiter or last waiter, we could del self._existence_waiter_locks[resource_id]
                # but defaultdict is fine.
                return obj

            # If still not found, get or create an event for this resource_id
            # defaultdict(asyncio.Event) creates a new event if key doesn't exist
            event = self._existence_waiters[resource_id]
            # Ensure event is clear if it was somehow set and not cleaned up by a previous waiter
            # This shouldn't happen if waiters clean up properly.
            if event.is_set():  # Should only be set if object was added
                if (obj := self._objects.get(resource_id)) is not None:
                    _LOGGER.debug(
                        "watch_exists: Event for %s was already set and object exists.",
                        resource_id,
                    )
                    # Clean up if we are done with this event for this waiter
                    if (
                        self._existence_waiters[resource_id] is event
                        and not event.is_set()
                    ):  # Check if it's still our event and not set
                        del self._existence_waiters[
                            resource_id
                        ]  # Or event.clear() if event is reused
                    return obj
                else:  # Event set but object not found - inconsistent state, clear and wait
                    _LOGGER.warning(
                        "watch_exists: Event for %s was set but object not found. Clearing and re-waiting.",
                        resource_id,
                    )
                    event.clear()

        _LOGGER.debug(
            "watch_exists: Object %s not in store, waiting for it to be added.",
            resource_id,
        )
        try:
            await event.wait()
            # After event is set, object should be in the store
            if (obj := self._objects.get(resource_id)) is not None:
                _LOGGER.debug(
                    "watch_exists: Object %s added to store and event triggered.",
                    resource_id,
                )
                return obj
            else:
                # This case should ideally not be reached if add_object correctly sets the event
                # and the event is not cleared prematurely.
                _LOGGER.error(
                    "watch_exists: Event for %s triggered, but object not found in store.",
                    resource_id,
                )
                raise ObjectNotFoundError(
                    f"watch_exists: Event for {resource_id} triggered, but object not found."
                )
        except asyncio.CancelledError:
            _LOGGER.debug("watch_exists for %s cancelled.", resource_id)
            raise
        finally:
            # Clean up the specific event and lock if this waiter is done with it.
            # This is important to prevent old events from affecting new waiters if the resource_id is watched again.
            async with self._existence_waiter_locks[resource_id]:
                if (
                    resource_id in self._existence_waiters
                    and self._existence_waiters[resource_id] is event
                ):
                    # If the event is set, it means it was triggered by add_object.
                    # If not set, it means we were cancelled before it was triggered.
                    # In either case, this specific waiter is done with this event instance.
                    del self._existence_waiters[resource_id]
                # Clean up the lock if no more waiters for this resource_id (heuristic)
                if (
                    resource_id not in self._existence_waiters
                    and resource_id in self._existence_waiter_locks
                ):
                    # Check if lock is still held by current task before deleting
                    # This part is tricky; defaultdict for locks is generally safe.
                    # For simplicity, let defaultdict manage lock creation/existence.
                    pass
