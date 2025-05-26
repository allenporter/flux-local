"""
Kustomization Controller implementation.

This controller manages the reconciliation of Kustomization resources, handling
the build and deployment of Kubernetes manifests from Kustomization definitions.
It follows Flux's controller pattern and integrates with the SourceController
for artifact management.

Key Concepts:
    - Kustomization: A resource that defines how to build Kubernetes manifests
      using Kustomize.
    - SourceController: Manages source artifacts (Git repositories, OCI images).
    - Store: Central state management for resource status and artifacts.

Dependencies:
    - flux_local.store.Store: For state management and artifact storage.
    - flux_local.manifest.NamedResource: For resource identification.
    - flux_local.manifest.Kustomization: For Kustomization resource handling.
    - flux_local.kustomize.flux_build: For building Kustomize manifests.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from flux_local.store import Store, Status, Artifact
from flux_local.source_controller.artifact import GitArtifact, OCIArtifact
from flux_local.manifest import (
    NamedResource,
    Kustomization,
    parse_raw_obj,
    KUSTOMIZE_KIND,
)
from flux_local.exceptions import (
    InputException,
    ResourceFailedError,
    DependencyFailedError,
)
from flux_local.kustomize import flux_build
from flux_local.task import get_task_service

from .artifact import KustomizationArtifact


_LOGGER = logging.getLogger(__name__)


class DependencyPendingError(Exception):
    """Exception raised when a Kustomization dependency is not ready."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class KustomizationController:
    """
    Controller for reconciling Kustomization resources.

    This controller watches for Kustomization objects in the store, resolves their
    dependencies, builds the kustomization, and stores the resulting manifests.
    """

    def __init__(self, store: Store) -> None:
        """
        Initialize the controller with a store.

        The controller is responsible for managing the reconciliation of Kustomization resources.
        It uses the provided store to manage state and artifacts.

        Args:
            store: The central store for managing state and artifacts
        """
        self._store = store
        self._tasks: list[asyncio.Task[None]] = []
        self._task_service = get_task_service()
        self._tasks.append(
            self._task_service.create_background_task(self._watch_kustomizations())
        )

    async def close(self) -> None:
        """Clean up any resources used by the controller.

        This method cancels all ongoing reconciliation tasks and waits for them
        to complete.
        """
        _LOGGER.info("Closing KustomizationController, cancelling tasks")
        for task in self._tasks:
            task.cancel()
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        self._tasks.clear()

    async def _watch_kustomizations(self) -> None:
        """Watch for Kustomization objects in the store and handle their addition."""
        _LOGGER.info("Watching for Kustomization objects in the store")
        async for resource_id, obj in self._store.watch_added(KUSTOMIZE_KIND):
            if not isinstance(obj, Kustomization):
                _LOGGER.warning(
                    "Received non-Kustomization object %s, skipping",
                    obj,
                )
                continue
            self._tasks.append(
                self._task_service.create_task(self.reconcile(resource_id, obj))
            )
        _LOGGER.info("Stopped watching for Kustomization objects")

    async def reconcile(
        self, resource_id: NamedResource, kustomization: Kustomization
    ) -> None:
        """
        Reconcile a Kustomization resource.

        This method performs the following steps:
        1. Checks dependencies using the new store.watch_ready API
        2. Resolves source artifacts, also using store.watch_ready for the source
        3. Builds the kustomization
        4. Stores the resulting manifests

        Args:
            resource_id: The identifier for the Kustomization resource
            kustomization: The Kustomization object to reconcile
        """
        _LOGGER.info("Reconciling Kustomization %s", resource_id)
        # Set initial status to PENDING. If reconcile fails before any watch_ready,
        # this status will persist. If it's due to a dependency, _check_dependencies
        # will update it more specifically.
        self._store.update_status(
            resource_id, Status.PENDING, "Starting reconciliation"
        )

        try:
            # 1. Check dependencies
            await self._check_dependencies(resource_id, kustomization)

            # 2. Resolve source artifacts
            source_path = await self._resolve_source(resource_id, kustomization)
        except DependencyPendingError as e:
            self._store.update_status(resource_id, Status.PENDING, error=e.message)
            return
        except (DependencyFailedError, InputException) as e:
            self._store.update_status(resource_id, Status.FAILED, error=str(e))
            return

        # 3. Build the kustomization
        try:
            manifests = await self._build_kustomization(source_path, kustomization)
        except InputException as e:
            self._store.update_status(resource_id, Status.FAILED, error=str(e))
            return

        try:
            _LOGGER.info("Applying Kustomization output %s", resource_id)
            await self._apply(manifests)

            # 4. Store results
            artifact = KustomizationArtifact(
                path=source_path,
                manifests=manifests,
                revision=getattr(kustomization, "revision", None),
            )
            self._store.set_artifact(resource_id, artifact)
            self._store.update_status(resource_id, Status.READY)
            _LOGGER.info("Successfully reconciled Kustomization %s", resource_id)
        except Exception as e:
            _LOGGER.error(
                "Uncaught exception while applying Kustomization %s: %s",
                resource_id.namespaced_name,
                e,
                exc_info=True,
            )
            self._store.update_status(resource_id, Status.FAILED, error=str(e))

    async def _check_dependencies(
        self, resource_id: NamedResource, kustomization: Kustomization
    ) -> None:
        """Verify all dependencies are ready using store.watch_ready.

        Args:
            resource_id: The Kustomization being reconciled.
            kustomization: The Kustomization object to check dependencies for.

        Raises:
            DependencyPendingError: If any dependency is not yet ready (still pending).
            DependencyFailedError: If any dependency has failed.
        """
        if not kustomization.depends_on:
            return

        dep_tasks = []
        dep_ids = []

        for dep_full_name in kustomization.depends_on:
            try:
                # Dependencies in depends_on can be 'name' (implicit same namespace) or 'namespace/name'
                dep_namespace, dep_name = dep_full_name.split("/", 1)
                dep_id = NamedResource(
                    kind="Kustomization", namespace=dep_namespace, name=dep_name
                )
            except ValueError as e:
                # Handle cases where splitting or parsing might fail if format is unexpected
                msg = (
                    f"Kustomization {resource_id.namespaced_name} has an invalid dependency format: "
                    f"'{dep_full_name}'. Expected 'namespace/name'. Error: {e}"
                )
                _LOGGER.error(msg)
                raise InputException(msg) from e

            dep_ids.append(dep_id)
            _LOGGER.debug(
                "Kustomization %s waiting for dependency %s to be ready.",
                resource_id.namespaced_name,
                dep_id.namespaced_name,
            )
            dep_tasks.append(self._store.watch_ready(dep_id))

        if not dep_tasks:
            return

        dep_strs = [dep.namespaced_name for dep in dep_ids]
        self._store.update_status(resource_id, Status.PENDING, f"Waiting on {dep_strs}")

        try:
            await asyncio.gather(*dep_tasks)
            _LOGGER.info(
                "All dependencies for Kustomization %s are ready.",
                resource_id.namespaced_name,
            )
        except ResourceFailedError as e:
            # A dependency has failed. This Kustomization should also be marked as Failed.
            _LOGGER.warning(
                "Dependency %s for Kustomization %s failed: %s",
                e.resource_name,
                resource_id.namespaced_name,
                e.message,
            )
            # Update this Kustomization's status to reflect the dependency failure.
            raise DependencyFailedError(
                kustomization_id=resource_id.namespaced_name,
                dependency_id=e.resource_name,
                dependency_error=e.message,
            ) from e
        except Exception as e:
            # This might catch other asyncio errors or unexpected issues from watch_ready.
            # For now, treat as a pending dependency, as we don't know the exact state.
            _LOGGER.error(
                "Unexpected error while waiting for dependencies of Kustomization %s: %s",
                resource_id.namespaced_name,
                e,
                exc_info=True,
            )
            pending_dep_names = [dep.namespaced_name for dep in dep_ids]
            raise DependencyPendingError(
                f"Kustomization {resource_id.namespaced_name} encountered an error while waiting for "
                f"dependencies {', '.join(pending_dep_names)}: {type(e).__name__}"
            ) from e

    async def _resolve_source(
        self, resource_id: NamedResource, kustomization: Kustomization
    ) -> str:
        """Resolve the source path for the kustomization, waiting if source is not ready.

        Args:
            resource_id: The Kustomization being reconciled.
            kustomization: The Kustomization to resolve the source for

        Returns:
            The resolved source path

        Raises:
            DependencyPendingError: If the source is not yet ready (still pending).
            DependencyFailedError: If the source has failed.
            InputException: If the source cannot be resolved or is invalid for other reasons.
        """
        if not kustomization.source_kind or not kustomization.source_name:
            _LOGGER.debug(
                "No sourceRef specified for %s, using path directly: %s",
                kustomization.namespaced_name,
                kustomization.path,
            )
            # Ensure the path exists if it's a local path without a sourceRef
            # This logic might need adjustment based on how local paths are meant to be handled.
            # For now, assume it's valid if provided.
            return kustomization.path

        source_ns = kustomization.source_namespace or kustomization.namespace
        source_ref_id = NamedResource(
            kind=kustomization.source_kind,
            namespace=source_ns,
            name=kustomization.source_name,
        )

        _LOGGER.info(
            "Kustomization %s waiting for source %s to be ready.",
            resource_id.namespaced_name,
            source_ref_id.namespaced_name,
        )
        self._store.update_status(
            resource_id, Status.PENDING, f"Waiting on {source_ref_id.namespaced_name}"
        )

        try:
            source_status = await self._store.watch_ready(source_ref_id)
        except ResourceFailedError as e:
            _LOGGER.warning(
                "Source %s for Kustomization %s failed: %s",
                e.resource_name,
                resource_id.namespaced_name,
                e.message,
            )
            raise DependencyFailedError(
                kustomization_id=resource_id.namespaced_name,
                dependency_id=e.resource_name,
                dependency_error=e.message,
            ) from e
        except Exception as e:
            # Catch other potential errors from watch_ready
            _LOGGER.error(
                "Unexpected error while waiting for source %s for Kustomization %s: %s",
                source_ref_id.namespaced_name,
                resource_id.namespaced_name,
                e,
                exc_info=True,
            )
            raise DependencyPendingError(
                f"Kustomization {resource_id.namespaced_name} encountered an error while waiting for "
                f"source {source_ref_id.namespaced_name}: {type(e).__name__}"
            ) from e

        _LOGGER.info(
            "Source %s for Kustomization %s is READY (status: %s).",
            source_ref_id.namespaced_name,
            resource_id.namespaced_name,
            source_status.status.value,
        )

        if (artifact := self._store.get_artifact(source_ref_id, Artifact)) is None:
            # This should ideally not happen if watch_ready succeeded and returned READY,
            # as READY implies an artifact should be present for sources.
            # However, to be safe, handle this case.
            msg = (
                f"Source artifact {source_ref_id.namespaced_name} not found "
                f"for Kustomization {resource_id.namespaced_name}, despite source being ready."
            )
            _LOGGER.error(msg)
            raise InputException(msg)

        # Build the full source path by joining the artifact path with the kustomization path
        if isinstance(artifact, (GitArtifact, OCIArtifact)):
            # Ensure kustomization.path is treated as relative to the artifact's root
            # and handles cases where kustomization.path might be '.' or empty.
            ks_path_part = kustomization.path.strip("./")
            if ks_path_part:
                source_path = str(Path(artifact.local_path) / ks_path_part)
            else:
                source_path = str(Path(artifact.local_path))
        else:
            msg = (
                f"Source artifact {source_ref_id.namespaced_name} for Kustomization {resource_id.namespaced_name} "
                f"is not a GitArtifact or OCIArtifact (type: {type(artifact).__name__})"
            )
            _LOGGER.error(msg)
            raise InputException(msg)

        _LOGGER.debug(
            "Resolved source path for Kustomization %s to %s",
            resource_id.namespaced_name,
            source_path,
        )
        return source_path

    async def _build_kustomization(
        self, source_path: str, kustomization: Kustomization
    ) -> list[dict[str, Any]]:
        """Build the kustomization and return the resulting manifests.

        This method uses the flux CLI to build the kustomization and returns
        the resulting Kubernetes manifests as a list of dictionaries.

        Args:
            source_path: Path to the source directory containing kustomization.yaml
            kustomization: The Kustomization to build

        Returns:
            List of rendered Kubernetes manifests

        Raises:
            InputException: If the build fails or the output cannot be parsed
        """
        _LOGGER.debug(
            "Building kustomization %s from path: %s",
            kustomization.namespaced_name,
            source_path,
        )

        try:
            # Create a Path object from the source_path
            path = Path(source_path)

            # Use the flux_build function to create a Kustomize instance
            kustomize = flux_build(kustomization, path)

            # Execute the build and get the resulting objects
            objects = await kustomize.objects(
                target_namespace=kustomization.target_namespace
            )

            _LOGGER.debug(
                "Successfully built %d objects for %s",
                len(objects),
                kustomization.namespaced_name,
            )
            return objects

        except Exception as e:
            error_msg = f"Failed to build kustomization {kustomization.namespaced_name}: {str(e)}"
            _LOGGER.exception(error_msg)
            raise InputException(error_msg) from e

    async def _apply(self, manifests: list[dict[str, Any]]) -> None:
        """Apply the manifests to the cluster."""
        _LOGGER.debug("Applying manifests: %s", manifests)
        for manifest in manifests:
            try:
                obj = parse_raw_obj(manifest)
            except ValueError as e:
                raise InputException(f"Failed to parse manifest: {manifest}") from e
            _LOGGER.debug("Applying %s", obj)
            self._store.add_object(obj)
