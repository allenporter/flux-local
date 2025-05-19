"""Source Controller module.

This controller manages the fetching and caching of source artifacts from
various repositories (Git and OCI). It provides a unified interface for
downstream controllers to access source code and artifacts needed for
building Kubernetes manifests.

Key Concepts:
    - GitRepository: Manages Git-based source repositories
    - OCIRepository: Manages OCI-based source repositories
    - Artifact: Represents a fetched source artifact with metadata

Supported Source Types:
    - GitRepository: For Git-based source code repositories
    - OCIRepository: For OCI-based container image repositories

Integration Points:
    - flux_local.store.Store: For state management and artifact storage
    - flux_local.manifest.GitRepository: For Git repository handling
    - flux_local.manifest.OCIRepository: For OCI repository handling
"""

import asyncio
import logging

from flux_local.store import Store, StoreEvent, Status
from flux_local.manifest import (
    NamedResource,
    BaseManifest,
    OCIRepository,
    GitRepository,
)
from flux_local.task import get_task_service


from .git import fetch_git
from .oci import fetch_oci

_LOGGER = logging.getLogger(__name__)


class SourceController:
    """
    Controller for managing source artifacts from Git and OCI repositories.

    This controller watches for GitRepository and OCIRepository objects in the
    store, fetches the source artifacts, and makes them available to other
    controllers like KustomizeController and HelmReleaseController.
    """

    SUPPORTED_KINDS = {"GitRepository", "OCIRepository"}

    def __init__(self, store: Store) -> None:
        """
        Initialize the source controller.

        Args:
            store: The central store for managing state and artifacts
        """
        self._store = store
        self._tasks: list[asyncio.Task[None]] = []
        self._task_service = get_task_service()

        # Wrap the sync event handler to schedule as a task
        def listener(resource_id: NamedResource, obj: BaseManifest) -> None:
            """Event listener for new source repository objects.

            This listener is triggered when a new source repository object
            (GitRepository or OCIRepository) is added to the store. It schedules
            a task to fetch the repository.

            Args:
                resource_id: The identifier for the source repository
                obj: The source repository object to handle
            """
            if resource_id.kind in self.SUPPORTED_KINDS:
                self._tasks.append(
                    self._task_service.create_task(
                        self.on_source_added(resource_id, obj)
                    )
                )

        self._store.add_listener(StoreEvent.OBJECT_ADDED, listener)

    async def close(self) -> None:
        """Clean up resources used by the controller.

        This method cancels all ongoing repository fetching tasks and waits
        for them to complete.
        """
        # Cancel all our tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Wait for all our tasks to complete
        await self._task_service.block_till_done()

    async def on_source_added(
        self, resource_id: NamedResource, obj: BaseManifest
    ) -> None:
        """Handle the addition of a new source repository to the store.

        Args:
            resource_id: The identifier for the source repository
            obj: The source repository object to handle
        """
        if not isinstance(obj, (GitRepository, OCIRepository)):
            _LOGGER.error(
                "Expected GitRepository or OCIRepository but got %s for %s",
                type(obj).__name__,
                resource_id,
            )
            return

        await self.fetch(resource_id, obj)

    async def fetch(self, resource_id: NamedResource, obj: BaseManifest) -> None:
        """Fetch a source artifact based on repository type.

        This method determines the type of source repository and delegates
        the fetching to the appropriate handler.

        Args:
            resource_id: The identifier for the source repository
            obj: The source repository object to fetch
        """
        if isinstance(obj, GitRepository):
            await self._fetch_git(resource_id, obj)
        elif isinstance(obj, OCIRepository):
            await self._fetch_oci(resource_id, obj)
        else:
            raise ValueError(f"Unsupported repository type: {type(obj).__name__}")

    async def _fetch_git(self, resource_id: NamedResource, obj: GitRepository) -> None:
        """Fetch a Git repository."""
        artifact = await fetch_git(obj)
        _LOGGER.info("Fetched Git repository %s", resource_id)
        self._store.set_artifact(resource_id, artifact)
        self._store.update_status(resource_id, Status.READY)

    async def _fetch_oci(self, resource_id: NamedResource, obj: OCIRepository) -> None:
        """Fetch an OCI repository."""
        artifact = await fetch_oci(obj)
        _LOGGER.info("Fetched OCI repository %s", resource_id)
        self._store.set_artifact(resource_id, artifact)
        self._store.update_status(resource_id, Status.READY)

    async def reconcile(self, resource_id: NamedResource, obj: BaseManifest) -> None:
        """Reconcile the source."""
        _LOGGER.info("Reconciling %s", resource_id)
        self._store.update_status(resource_id, Status.PENDING)
        try:
            await self.fetch(resource_id, obj)
            _LOGGER.info("Reconciled %s", resource_id)
            self._store.update_status(resource_id, Status.READY)
        except Exception as e:
            _LOGGER.error("Failed to reconcile %s: %s", resource_id, e)
            self._store.update_status(resource_id, Status.FAILED, error=str(e))
