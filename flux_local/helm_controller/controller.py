"""HelmRelease Controller implementation.

This controller manages the reconciliation of HelmRelease resources,
using the existing Helm class for chart operations and template rendering.

Key Concepts:
    - HelmRelease: A resource that defines how to deploy a Helm chart
    - Store: Central state management for resource status and artifacts
"""

import asyncio
import logging
from typing import Any
from dataclasses import dataclass

from flux_local.exceptions import HelmException, InputException
from flux_local.helm import Helm, Options, LocalGitRepository
from flux_local.manifest import (
    NamedResource,
    BaseManifest,
    HelmRelease,
    CONFIG_MAP_KIND,
    SECRET_KIND,
    GitRepository,
    OCIRepository,
    HelmRepository,
    parse_raw_obj,
)
from flux_local.store import Store, StoreEvent, Status, Artifact
from flux_local.source_controller import GitArtifact
from flux_local.task import get_task_service

from .artifact import HelmReleaseArtifact

_LOGGER = logging.getLogger(__name__)


@dataclass
class HelmControllerConfig:
    """Configuration for the HelmReleaseController."""

    wipe_secrets: bool = True


class HelmControllerException(Exception):
    """Exception raised by the Helm controller."""


class HelmReleaseController:
    """
    Controller for reconciling HelmRelease resources.

    This controller watches for HelmRelease objects in the store and uses
    the existing Helm class for chart operations and template rendering.
    """

    def __init__(self, store: Store, helm: Helm, config: HelmControllerConfig) -> None:
        """
        Initialize the controller with a store and Helm instance.

        Args:
            store: The central store for managing state and artifacts
            helm: The Helm instance for chart operations
            config: The configuration for the controller
        """
        self.store = store
        self.helm = helm
        self._config = config
        self._tasks: list[asyncio.Task[None]] = []
        self._task_service = get_task_service()
        self._need_update = False

        self.store.add_listener(
            StoreEvent.OBJECT_ADDED, self._added_listener, flush=True
        )
        self.store.add_listener(
            StoreEvent.ARTIFACT_UPDATED, self._artifact_listener, flush=True
        )

    def _added_listener(self, resource_id: NamedResource, obj: BaseManifest) -> None:
        """Event listener for new repository objects."""
        if resource_id.kind == "HelmRepository" and isinstance(obj, HelmRepository):
            self._need_update = True
            self.helm.add_repo(obj)
        if resource_id.kind == "OCIRepository" and isinstance(obj, OCIRepository):
            self._need_update = True
            self.helm.add_repo(obj)
        if resource_id.kind == "HelmRelease":
            self._tasks.append(
                self._task_service.create_task(
                    self.on_helm_release_added(resource_id, obj)
                )
            )

    def _artifact_listener(
        self, resource_id: NamedResource, artifact: Artifact
    ) -> None:
        """Event listener for new GitRepository GitArtifact objects."""
        _LOGGER.debug(
            "GitRepository %s artifact updated, artifact=%s", resource_id, artifact
        )
        if resource_id.kind != "GitRepository" or not isinstance(artifact, GitArtifact):
            return
        if not (git_repo := self.store.get_object(resource_id, GitRepository)):
            _LOGGER.error("GitRepository %s not found", resource_id)
            return
        self._need_update = True
        self.helm.add_repo(LocalGitRepository(repo=git_repo, artifact=artifact))

    async def close(self) -> None:
        """Clean up resources used by the controller."""
        # Cancel all our tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def on_helm_release_added(
        self, resource_id: NamedResource, obj: BaseManifest
    ) -> None:
        """Handle the addition of a new HelmRelease to the store."""
        if not isinstance(obj, HelmRelease):
            _LOGGER.error(
                "Received non-HelmRelease object for HelmRelease controller: %s",
                resource_id,
            )
            return

        try:
            await self.reconcile_helm_release(resource_id, obj)
        except HelmControllerException as e:
            _LOGGER.warning(
                "Failed to reconcile HelmRelease %s: %s", resource_id, str(e)
            )
            # Update status with error
            self.store.update_status(
                resource_id,
                Status.FAILED,
                error=f"Reconciliation failed: {type(e).__name__}: {str(e)}",
            )
        except Exception as e:
            _LOGGER.exception(
                "Failed to reconcile HelmRelease %s: %s", resource_id, str(e)
            )
            # Update status with error
            self.store.update_status(
                resource_id,
                Status.FAILED,
                error=f"Reconciliation failed: {type(e).__name__}: {str(e)}",
            )

    async def reconcile_helm_release(
        self, resource_id: NamedResource, helm_release: HelmRelease
    ) -> None:
        """
        Reconcile a HelmRelease resource using the existing Helm class.

        This method handles:
        1. Dependency resolution
        2. Chart operations and template rendering
        3. Status management
        """
        _LOGGER.info("Reconciling HelmRelease %s", resource_id)

        # Update status to processing
        self.store.update_status(resource_id, Status.PENDING)

        # Wait for dependencies to be ready
        await self.wait_for_dependencies(helm_release)

        if self._need_update:
            _LOGGER.info("Updating Helm repositories")
            await self.helm.update()
            self._need_update = False

        # TODO: Exercise ValuesFrom logic

        # Prepare options using only the options that exist in HelmRelease
        options = Options(
            skip_crds=True,  # Default to skipping CRDs
            skip_tests=True,  # Default to skipping tests
            skip_secrets=True,  # Default to skipping secrets
        )

        # Use Helm's template functionality
        # TODO: Add a flag to limit helm concurrency for buggy helm clients
        try:
            kustomize = await self.helm.template(helm_release, options)
        except HelmException as err:
            raise HelmControllerException(
                f"Failed to template HelmRelease {resource_id}: {err}"
            ) from err
        objects = await kustomize.objects()
        _LOGGER.info(
            "Chart %s rendered %d objects", helm_release.chart.name, len(objects)
        )
        await self._apply(objects)

        # Store the result
        artifact = HelmReleaseArtifact(
            chart_name=helm_release.chart.chart_name,
            objects=objects,
            values=helm_release.values or {},
        )
        self.store.set_artifact(resource_id, artifact)
        self.store.update_status(resource_id, Status.READY)

    async def wait_for_dependencies(self, helm_release: HelmRelease) -> None:
        """Wait for all dependencies to be ready."""
        # Get all ConfigMaps and Secrets that this HelmRelease depends on
        dependencies = set()
        if helm_release.values_from:
            for ref in helm_release.values_from:
                if ref.kind == CONFIG_MAP_KIND:
                    dependencies.add(
                        NamedResource(
                            kind=CONFIG_MAP_KIND,
                            namespace=helm_release.namespace,
                            name=ref.name,
                        )
                    )
                elif ref.kind == SECRET_KIND:
                    dependencies.add(
                        NamedResource(
                            kind=SECRET_KIND,
                            namespace=helm_release.namespace,
                            name=ref.name,
                        )
                    )
        if helm_release.chart:
            dependencies.add(
                NamedResource(
                    kind=helm_release.chart.repo_kind,
                    namespace=helm_release.chart.repo_namespace,
                    name=helm_release.chart.repo_name,
                )
            )

        # Wait for each dependency to be ready. Log the tasks that are not complete.
        if dependencies:
            tasks = []
            for dep in dependencies:
                if dep.kind == GitRepository.kind:
                    tasks.append(
                        self._task_service.create_task(
                            self.wait_for_resource_ready(dep),
                            name=f"{str(dep)} ready",
                        )
                    )
                else:
                    tasks.append(
                        self._task_service.create_task(
                            self.wait_for_resource_exists(dep),
                            name=f"{str(dep)} exists",
                        )
                    )
            _LOGGER.info(
                "Waiting for dependencies: %s", [task.get_name() for task in tasks]
            )
            # TODO: End sooner on explicit error rather than waiting for timeout
            try:
                async with asyncio.timeout(5):
                    await asyncio.gather(*tasks)
            except asyncio.TimeoutError as err:
                _LOGGER.error(
                    "Timeout waiting for remaining dependencies: %s",
                    [
                        task.get_name()
                        for task in tasks
                        if not task.done() or task.cancelled()
                    ],
                )
                raise err
            else:
                _LOGGER.info(
                    "All dependencies of %s are ready: %s",
                    helm_release.namespaced_name,
                    dependencies,
                )

    async def wait_for_resource_ready(self, resource: NamedResource) -> None:
        """Wait for a resource to be ready."""
        status = self.store.get_status(resource)
        if status:
            if status.status == Status.READY:
                return
            elif status.status == Status.FAILED:
                raise HelmControllerException(f"Dependency {resource}: {status}")

        reconcile_event = asyncio.Event()

        def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
            if resource_id != resource:
                return
            status = self.store.get_status(resource_id)
            if status and status.status == Status.READY:
                _LOGGER.info("Resource %s is ready", resource_id)
                reconcile_event.set()
            else:
                _LOGGER.info("Resource %s is not ready", resource_id)

        remove_listener = self.store.add_listener(
            StoreEvent.STATUS_UPDATED, on_status_updated
        )

        try:
            await reconcile_event.wait()
        finally:
            remove_listener()

    async def wait_for_resource_exists(self, resource: NamedResource) -> None:
        """Wait for a resource to exist."""
        _LOGGER.debug("Waiting for resource %s to exist", resource)
        if self.store.get_object(resource, BaseManifest):
            _LOGGER.debug("Resource %s exists", resource)
            return

        # Create an event to signal when the resource is ready
        exists_event = asyncio.Event()

        # Add a listener for status updates
        def on_object_added(resource_id: NamedResource, obj: BaseManifest) -> None:
            if resource_id == resource:
                if self.store.get_object(resource_id, BaseManifest):
                    _LOGGER.debug("Resource now %s exists", resource_id)
                    exists_event.set()
                else:
                    _LOGGER.debug("Resource %s does not exist", resource_id)

        # Register the listener
        remove_listener = self.store.add_listener(
            StoreEvent.OBJECT_ADDED, on_object_added
        )

        try:
            # Wait for the resource to exist
            await exists_event.wait()
        finally:
            # Clean up the listener
            remove_listener()

    async def _apply(self, manifests: list[dict[str, Any]]) -> None:
        """Apply the manifests to the cluster."""
        _LOGGER.debug("Applying manifests: %s", manifests)
        for manifest in manifests:
            try:
                obj = parse_raw_obj(manifest, wipe_secrets=self._config.wipe_secrets)
            except ValueError as e:
                raise InputException(f"Failed to parse manifest: {manifest}") from e
            _LOGGER.debug("Applying %s", obj)
            self.store.add_object(obj)
