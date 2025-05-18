"""The source controller module."""

import asyncio
import logging
from typing import Any

from flux_local.store import Store, StoreEvent, Status
from flux_local.manifest import (
    NamedResource,
    BaseManifest,
    OCIRepository,
    GitRepository,
)

from .git import fetch_git
from .oci import fetch_oci

_LOGGER = logging.getLogger(__name__)


class SourceController:
    SUPPORTED_KINDS = {"GitRepository", "OCIRepository"}

    def __init__(self, store: Store) -> None:
        """Initialize the source controller."""
        self.store = store
        self._tasks: list[asyncio.Task[None]] = []

        # Wrap the sync event handler to schedule as a task
        def listener(resource_id: NamedResource, obj: BaseManifest) -> None:
            self._tasks.append(
                asyncio.create_task(self.on_object_added(resource_id, obj))
            )

        self.store.add_listener(StoreEvent.OBJECT_ADDED, listener)

    async def close(self) -> None:
        """Close the source controller."""
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def on_object_added(
        self, resource_id: NamedResource, obj: BaseManifest
    ) -> None:
        """Handle the addition of a new object to the store."""
        # TODO: Add a `kind` attribute to BaseManifest
        if getattr(obj, "kind", None) not in self.SUPPORTED_KINDS:
            return
        await self.reconcile(resource_id, obj)

    async def reconcile(self, resource_id: NamedResource, obj: BaseManifest) -> None:
        """Reconcile the source."""
        _LOGGER.info("Reconciling %s", resource_id)
        self.store.update_status(resource_id, Status.PENDING)
        try:
            kind = getattr(obj, "kind", None)
            artifact: Any
            if kind == "GitRepository" and isinstance(obj, GitRepository):
                artifact = await fetch_git(obj)
            elif kind == "OCIRepository" and isinstance(obj, OCIRepository):
                artifact = await fetch_oci(obj)
            else:
                raise ValueError(f"Unsupported kind: {kind} (type: {type(obj)})")
            _LOGGER.info("Reconciled %s", resource_id)
            self.store.set_artifact(resource_id, artifact)
            self.store.update_status(resource_id, Status.READY)
        except Exception as e:
            _LOGGER.error("Failed to reconcile %s: %s", resource_id, e)
            self.store.update_status(resource_id, Status.FAILED, error=str(e))
