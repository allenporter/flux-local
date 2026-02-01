"""Common build utilities for flux-local."""

import logging
import pathlib
from typing import Any, Callable
import yaml

from flux_local import git_repo
from flux_local.manifest import (
    BaseManifest,
    NamedResource,
    strip_resource_attributes,
    STRIP_ATTRIBUTES,
    HelmRelease,
    Kustomization,
)
from flux_local.helm_controller.artifact import HelmReleaseArtifact
from flux_local.kustomize_controller.artifact import KustomizationArtifact
from flux_local.exceptions import FluxException
from flux_local.orchestrator import BootstrapOptions, Orchestrator, OrchestratorConfig
from flux_local.store import InMemoryStore, Status

from .format import open_file

_LOGGER = logging.getLogger(__name__)


def filter_manifest(doc: dict[str, Any], **kwargs: Any) -> bool:
    """Return true if the manifest should be included in the output."""
    if kwargs.get("skip_crds") and doc.get("kind") == "CustomResourceDefinition":
        return False
    if kwargs.get("skip_secrets") and doc.get("kind") == "Secret":
        return False
    if (skip_kinds := kwargs.get("skip_kinds")) and isinstance(skip_kinds, list):
        if doc.get("kind") in skip_kinds:
            return False
    return True


class BuildRunner:
    """Common build runner for HelmReleases and Kustomizations."""

    def __init__(
        self,
        config: OrchestratorConfig,
        resource_kind: str,
        resource_type: type[HelmRelease] | type[Kustomization],
        artifact_type: type[HelmReleaseArtifact] | type[KustomizationArtifact],
        selector_predicate: Callable[[BaseManifest], bool],
    ) -> None:
        self.config = config
        self.resource_kind = resource_kind
        self.resource_type = resource_type
        self.artifact_type = artifact_type
        self.selector_predicate = selector_predicate

    def _process_manifest(
        self, store: InMemoryStore, resource_id: NamedResource, **kwargs: Any
    ) -> list[dict[str, Any]] | None:
        """Process a single resource and return its manifests if ready."""
        status = store.get_status(resource_id)
        if not status:
            _LOGGER.warning(
                "%s %s has no status in the store", self.resource_kind, resource_id
            )
            return None

        if status.status != Status.READY:
            _LOGGER.error(
                "%s %s failed: %s", self.resource_kind, resource_id, status.error
            )
            return None

        artifact = store.get_artifact(resource_id, self.artifact_type)
        if not artifact or not artifact.manifests:
            _LOGGER.warning(
                "%s %s is Ready but has no artifact or manifests",
                self.resource_kind,
                resource_id,
            )
            return None

        _LOGGER.info(
            "Found %d manifests for %s %s",
            len(artifact.manifests),
            self.resource_kind,
            resource_id,
        )
        return [
            manifest_item
            for manifest_item in artifact.manifests
            if filter_manifest(manifest_item, **kwargs)
        ]

    async def run(
        self,
        path: pathlib.Path,
        output_file: str,
        builder: git_repo.CachableBuilder | None = None,
        **kwargs: Any,
    ) -> None:
        """Async Action implementation."""
        _LOGGER.info(
            "Building %ss from path %s using new orchestrator", self.resource_kind, path
        )

        store = InMemoryStore()
        orchestrator = Orchestrator(store, self.config)
        bootstrap_options = BootstrapOptions(path=path)
        try:
            await orchestrator.bootstrap(bootstrap_options)
        except FluxException as err:
            raise FluxException(
                f"Orchestrator bootstrap failed for path {path}"
            ) from err

        manifest_found = False
        manifest_match = False

        with open_file(output_file, "w") as file:
            for manifest_obj in store.list_objects(kind=self.resource_kind):
                if not isinstance(manifest_obj, self.resource_type):
                    continue
                # We can remove these type ignores once we define kind/name/namespace
                # in the BaseManifest.
                resource_id = NamedResource(
                    kind=manifest_obj.kind,  # type: ignore[attr-defined]
                    name=manifest_obj.name,  # type: ignore[attr-defined]
                    namespace=manifest_obj.namespace,  # type: ignore[attr-defined]
                )

                if not self.selector_predicate(manifest_obj):
                    _LOGGER.debug(
                        "%s %s did not match selector", self.resource_kind, resource_id
                    )
                    continue

                manifest_found = True
                manifests = self._process_manifest(store, resource_id, **kwargs)
                if not manifests:
                    continue
                manifest_match = True

                for manifest_item in manifests:
                    strip_resource_attributes(
                        manifest_item,
                        STRIP_ATTRIBUTES,
                    )
                    yaml.dump(manifest_item, file, sort_keys=False, explicit_start=True)

            if not manifest_match:
                if not manifest_found:
                    _LOGGER.warning(
                        "No %ss found or processed from path %s that matched selector",
                        self.resource_kind,
                        path,
                    )
                else:
                    _LOGGER.warning(
                        "No %ss that matched the selector were successfully built from path %s",
                        self.resource_kind,
                        path,
                    )
