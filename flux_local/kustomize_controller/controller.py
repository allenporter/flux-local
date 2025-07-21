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
from typing import Any, cast
from dataclasses import dataclass

from flux_local.store import Store, Status, Artifact
from flux_local.source_controller.artifact import GitArtifact, OCIArtifact
from flux_local.manifest import (
    NamedResource,
    Kustomization,
    parse_raw_obj,
    KUSTOMIZE_KIND,
    Secret,
    ConfigMap,
    SECRET_KIND,
    CONFIG_MAP_KIND,
)
from flux_local.exceptions import (
    InputException,
    KustomizeException,
)
from flux_local.kustomize import flux_build
from flux_local.task import get_task_service
from flux_local.store.watcher import (
    DependencyWaiter,
    DependencyState,
)
from flux_local import values

from .artifact import KustomizationArtifact


_LOGGER = logging.getLogger(__name__)

WAIT_TIMEOUT = 45
DEPENDENCY_RESOLUTION_TIMEOUT = 120


@dataclass
class KustomizationControllerConfig:
    """Configuration for the KustomizationController."""

    wipe_secrets: bool = True


class KustomizationController:
    """
    Controller for reconciling Kustomization resources.

    This controller watches for Kustomization objects in the store, resolves their
    dependencies, builds the kustomization, and stores the resulting manifests.
    """

    def __init__(self, store: Store, config: KustomizationControllerConfig) -> None:
        """
        Initialize the controller with a store.

        The controller is responsible for managing the reconciliation of Kustomization resources.
        It uses the provided store to manage state and artifacts.

        Args:
            store: The central store for managing state and artifacts
            config: The configuration for the controller
        """
        self._store = store
        self._config = config
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
        1. Waits for all dependencies (Kustomizations and Source) to be ready using DependencyWaiter.
        2. Resolves the source artifact path.
        3. Builds the kustomization.
        4. Stores the resulting manifests.

        Args:
            resource_id: The identifier for the Kustomization resource.
            kustomization: The Kustomization object to reconcile.
        """
        _LOGGER.info("Reconciling Kustomization %s", resource_id)
        self._store.update_status(
            resource_id, Status.PENDING, "Starting reconciliation"
        )

        dependency_waiter = DependencyWaiter(
            self._store, self._task_service, parent_resource_id=resource_id
        )
        source_ref_id: NamedResource | None = None

        # 1. Gather dependencies and add to waiter
        try:
            for dep_full_name in kustomization.depends_on or ():
                dep_namespace, dep_name = dep_full_name.split("/", 1)
                dep_id = NamedResource(
                    kind=KUSTOMIZE_KIND, namespace=dep_namespace, name=dep_name
                )
                dependency_waiter.add(dep_id)

            if kustomization.source_kind and kustomization.source_name:
                source_ns = kustomization.source_namespace or kustomization.namespace
                source_ref_id = NamedResource(
                    kind=kustomization.source_kind,
                    namespace=source_ns,
                    name=kustomization.source_name,
                )
                dependency_waiter.add(source_ref_id)

            # Block on any post-build substitution from config or secrets
            for sub_ref in kustomization.postbuild_substitute_from or ():
                if not sub_ref.kind or not sub_ref.name:
                    raise InputException(
                        f"Invalid postbuild substitute reference in Kustomization {kustomization.namespaced_name}: "
                        f"kind and name must be specified."
                    )
                sub_ref_id = NamedResource(
                    kind=sub_ref.kind,
                    namespace=kustomization.namespace,
                    name=sub_ref.name,
                )
                if not sub_ref.optional:
                    dependency_waiter.add(sub_ref_id)

        except InputException as e:
            self._store.update_status(resource_id, Status.FAILED, error=str(e))
            return

        source_path: str  # Will be set after dependency resolution or for local paths

        _LOGGER.info(
            "Kustomization %s waiting for %d dependencies...",
            resource_id.namespaced_name,
            dependency_waiter.get_summary().total_dependencies,
        )
        try:
            async with asyncio.timeout(DEPENDENCY_RESOLUTION_TIMEOUT):
                async for event in dependency_waiter.watch():
                    summary = dependency_waiter.get_summary()
                    if event.state not in (
                        DependencyState.FAILED,
                        DependencyState.TIMEOUT,
                    ):
                        self._store.update_status(
                            resource_id, Status.PENDING, summary.summary_message
                        )

                    if (
                        event.state == DependencyState.FAILED
                        or event.state == DependencyState.TIMEOUT
                    ):
                        self._store.update_status(
                            resource_id,
                            Status.FAILED,
                            summary.summary_message,
                        )
                        await dependency_waiter.cancel_pending_watches()
                        return

            final_summary = dependency_waiter.get_summary()
            if not final_summary.all_ready:
                self._store.update_status(
                    resource_id, Status.FAILED, error=final_summary.summary_message
                )
                return

        except asyncio.TimeoutError:
            _LOGGER.error(
                "Overall timeout (%s sec) reached while waiting for dependencies of Kustomization %s.",
                DEPENDENCY_RESOLUTION_TIMEOUT,
                resource_id.namespaced_name,
            )
            summary_on_timeout = dependency_waiter.get_summary()
            self._store.update_status(
                resource_id,
                Status.FAILED,
                error=f"Timed out waiting for dependencies: {summary_on_timeout.summary_message}",
            )
            await dependency_waiter.cancel_pending_watches()
            return
        except Exception as e:
            _LOGGER.error(
                "Unexpected error during dependency waiting for Kustomization %s: %s",
                resource_id.namespaced_name,
                e,
                exc_info=True,
            )
            self._store.update_status(
                resource_id,
                Status.FAILED,
                error=f"Unexpected error resolving dependencies: {type(e).__name__}",
            )
            await dependency_waiter.cancel_pending_watches()
            return

        _LOGGER.info(
            "All dependencies for Kustomization %s are ready.",
            resource_id.namespaced_name,
        )

        # 2. Resolve source path
        try:
            if source_ref_id:  # Source was defined (and waited for, if waiter was used)
                # If waiter was used and completed successfully, source_ref_id is READY.
                if (
                    artifact := self._store.get_artifact(source_ref_id, Artifact)
                ) is None:
                    # This implies source_ref_id might not be ready or artifact missing post-ready.
                    # DependencyWaiter should ensure it's ready if it was watched.
                    # If it wasn't watched (e.g. only kustomization.path used), this check is vital.
                    # However, if source_ref_id is set, it means it was added to the waiter.
                    msg = (
                        f"Source artifact {source_ref_id.namespaced_name} not found "
                        f"for Kustomization {resource_id.namespaced_name}, though it was expected to be ready."
                    )
                    self._store.update_status(resource_id, Status.FAILED, error=msg)
                    return

                if isinstance(artifact, (GitArtifact, OCIArtifact)):
                    ks_path_part = kustomization.path.strip("./")
                    # Ensure Path objects are used for joining
                    resolved_path = Path(artifact.local_path)
                    if (
                        ks_path_part
                    ):  # Avoid joining with '.' or empty string if ks_path_part is such
                        resolved_path = resolved_path / ks_path_part
                    source_path = str(resolved_path)
                    _LOGGER.debug(
                        "Resolved source path for Kustomization %s to %s via sourceRef %s",
                        resource_id.namespaced_name,
                        source_path,
                        source_ref_id.namespaced_name,
                    )
                else:
                    msg = (
                        f"Source artifact {source_ref_id.namespaced_name} for Kustomization {resource_id.namespaced_name} "
                        f"is not a GitArtifact or OCIArtifact (type: {type(artifact).__name__})"
                    )
                    self._store.update_status(resource_id, Status.FAILED, error=msg)
                    return
            elif kustomization.path:  # No sourceRef, use path directly
                _LOGGER.debug(
                    "No sourceRef specified for Kustomization %s, using path directly: %s",
                    kustomization.namespaced_name,
                    kustomization.path,
                )
                source_path = kustomization.path
            else:
                msg = f"Kustomization {resource_id.namespaced_name} has neither sourceRef nor a local path specified."
                self._store.update_status(resource_id, Status.FAILED, error=msg)
                return
        except InputException as e:
            self._store.update_status(resource_id, Status.FAILED, error=str(e))
            return
        except Exception as e:
            self._store.update_status(
                resource_id,
                Status.FAILED,
                error=f"Unexpected error resolving source path: {type(e).__name__}",
            )
            return

        # Load values for substitution
        if kustomization.postbuild_substitute_from:
            values.expand_postbuild_substitute_reference(
                kustomization,
                cluster_config_store(self._store),
            )

        # 3. Build the kustomization
        self._store.update_status(
            resource_id, Status.PENDING, f"Building Kustomization from {source_path}"
        )

        try:
            manifests = await self._build_kustomization(source_path, kustomization)
        except InputException as e:
            self._store.update_status(resource_id, Status.FAILED, error=str(e))
            return

        self._store.update_status(
            resource_id, Status.PENDING, f"Applying {len(manifests)} object manifests"
        )
        try:
            _LOGGER.info("Applying Kustomization output %s", resource_id)
            await self._apply(manifests)

            # 4. Store results
            artifact = KustomizationArtifact(
                path=source_path,  # source_path is now guaranteed to be a string
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
            path = Path(source_path)
            kustomize = flux_build(kustomization, path)
            objects = await kustomize.objects(
                target_namespace=kustomization.target_namespace
            )

            _LOGGER.debug(
                "Successfully built %d objects for %s",
                len(objects),
                kustomization.namespaced_name,
            )
            return objects

        except KustomizeException as e:
            error_msg = f"Failed to build kustomization {kustomization.namespaced_name}: {str(e)}"
            _LOGGER.info(error_msg)
            raise InputException(error_msg) from e

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


def cluster_config_store(store: Store) -> values.ClusterConfig:
    """Create a ClusterConfig from the store's secrets and configmaps."""
    return values.ClusterConfig(
        lambda: cast(list[Secret], store.list_objects(SECRET_KIND)),
        lambda: cast(list[ConfigMap], store.list_objects(CONFIG_MAP_KIND)),
    )
