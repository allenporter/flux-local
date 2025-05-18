"""Kustomization Controller implementation."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from flux_local.store import Store, StoreEvent, Status, Artifact
from flux_local.manifest import NamedResource, BaseManifest, Kustomization
from flux_local.exceptions import InputException
from flux_local.kustomize import flux_build

from .artifact import KustomizationArtifact

_LOGGER = logging.getLogger(__name__)


class KustomizationController:
    """
    Controller for reconciling Kustomization resources.

    The controller watches for Kustomization objects in the store, resolves their
    dependencies, builds the kustomization, and stores the resulting manifests.
    """

    def __init__(self, store: Store) -> None:
        """Initialize the controller with a store.

        Args:
            store: The central store for managing state and artifacts
        """
        self.store = store
        self._tasks: list[asyncio.Task[None]] = []

        def listener(resource_id: NamedResource, obj: BaseManifest) -> None:
            if resource_id.kind == "Kustomization":
                self._tasks.append(
                    asyncio.create_task(self.on_kustomization_added(resource_id, obj))
                )

        self.store.add_listener(StoreEvent.OBJECT_ADDED, listener)

    async def close(self) -> None:
        """Clean up any resources used by the controller."""
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def on_kustomization_added(
        self, resource_id: NamedResource, obj: BaseManifest
    ) -> None:
        """Handle the addition of a new Kustomization to the store."""
        if not isinstance(obj, Kustomization):
            _LOGGER.error(
                "Expected Kustomization but got %s for %s",
                type(obj).__name__,
                resource_id,
            )
            return

        await self.reconcile(resource_id, obj)

    async def reconcile(
        self, resource_id: NamedResource, kustomization: Kustomization
    ) -> None:
        """
        Reconcile a Kustomization resource.

        This will:
        1. Check dependencies
        2. Resolve source artifacts
        3. Build the kustomization
        4. Store the resulting manifests

        Args:
            resource_id: The identifier for the Kustomization resource
            kustomization: The Kustomization object to reconcile
        """
        _LOGGER.info("Reconciling Kustomization %s", resource_id)
        self.store.update_status(resource_id, Status.PENDING)

        try:
            # 1. Check dependencies
            if kustomization.depends_on:
                await self._check_dependencies(kustomization)

            # 2. Resolve source artifacts
            source_path = await self._resolve_source(kustomization)

            # 3. Build the kustomization
            manifests = await self._build_kustomization(source_path, kustomization)

            # 4. Store results
            artifact = KustomizationArtifact(
                path=source_path,
                manifests=manifests,
                revision=getattr(kustomization, "revision", None),
            )
            self.store.set_artifact(resource_id, artifact)
            self.store.update_status(resource_id, Status.READY)
            _LOGGER.info("Successfully reconciled Kustomization %s", resource_id)

        except Exception as e:
            _LOGGER.error("Failed to reconcile Kustomization %s: %s", resource_id, e)
            self.store.update_status(resource_id, Status.FAILED, error=str(e))

    async def _check_dependencies(self, kustomization: Kustomization) -> None:
        """Verify all dependencies are ready.

        Args:
            kustomization: The Kustomization to check dependencies for

        Raises:
            InputException: If any dependency is not ready or not found
        """
        if not kustomization.depends_on:
            return

        pending_deps = []
        failed_deps = []
        missing_deps = []

        for dep_name in kustomization.depends_on:
            # Create a NamedResource for the dependency
            # Note: Dependencies in depends_on are just names, we assume same namespace
            dep_namespace_name, dep_name = dep_name.split("/")
            dep_id = NamedResource(
                kind="Kustomization", namespace=dep_namespace_name, name=dep_name
            )

            # Get the status from the store
            status = self.store.get_status(dep_id)

            if status is None:
                missing_deps.append(dep_name)
                continue

            if status.status == Status.FAILED:
                failed_deps.append((dep_name, status.error))
            elif status.status != Status.READY:
                pending_deps.append(dep_name)

        # Build error messages if any dependencies are not ready
        error_msgs = []

        if missing_deps:
            error_msgs.append(f"Dependencies not found: {', '.join(missing_deps)}")

        if failed_deps:
            failed_msgs = [
                f"{name} ({error or 'no error message'})" for name, error in failed_deps
            ]
            error_msgs.append(f"Dependencies failed: {', '.join(failed_msgs)}")

        if pending_deps:
            error_msgs.append(f"Dependencies not ready: {', '.join(pending_deps)}")

        if error_msgs:
            raise InputException(
                f"Kustomization {kustomization.namespaced_name} has unresolved dependencies: "
                + "; ".join(error_msgs)
            )

    async def _resolve_source(self, kustomization: Kustomization) -> str:
        """Resolve the source path for the kustomization.

        This method resolves the source path for a Kustomization by checking:
        1. If the Kustomization has a sourceRef, it looks up the corresponding source artifact
        2. If no sourceRef is present, it assumes the path is relative to the current working directory

        Args:
            kustomization: The Kustomization to resolve the source for

        Returns:
            The resolved source path

        Raises:
            InputException: If the source cannot be resolved or is invalid
        """
        # If there's no source reference, assume the path is relative to the current directory
        if not kustomization.source_kind or not kustomization.source_name:
            _LOGGER.debug(
                "No sourceRef specified for %s, using path directly",
                kustomization.namespaced_name,
            )
            return kustomization.path

        # Create a NamedResource for the source reference
        source_ns = kustomization.source_namespace or kustomization.namespace
        source_ref = NamedResource(
            kind=kustomization.source_kind,
            namespace=source_ns,
            name=kustomization.source_name,
        )

        # Get the source artifact from the store
        artifact = self.store.get_artifact(source_ref, Artifact)
        if not artifact:
            raise InputException(
                f"Source artifact {kustomization.source_kind}/{kustomization.source_name} "
                f"not found in namespace '{source_ns}'"
            )

        # Verify the source status is ready
        source_status = self.store.get_status(source_ref)
        if not source_status or source_status.status != Status.READY:
            status_msg = source_status.status.value if source_status else "not found"
            raise InputException(
                f"Source {kustomization.source_kind}/{kustomization.source_name} is not ready "
                f"(status: {status_msg}): {source_status.error if source_status else ''}"
            )

        # Build the full source path by joining the artifact path with the kustomization path
        source_path = str(Path(artifact.path) / kustomization.path.strip("/"))
        _LOGGER.debug(
            "Resolved source path for %s to %s",
            kustomization.namespaced_name,
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
            _LOGGER.error(error_msg)
            raise InputException(error_msg) from e
