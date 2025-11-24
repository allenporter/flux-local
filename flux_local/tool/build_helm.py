"""Flux-local build for HelmReleases using the new Orchestrator."""

import logging
import pathlib
from argparse import (
    ArgumentParser,
    _SubParsersAction as SubParsersAction,
    BooleanOptionalAction,
)
from typing import Any, cast
import yaml

from flux_local import git_repo
from flux_local.helm_controller.artifact import HelmReleaseArtifact
from flux_local.manifest import (
    HELM_RELEASE,
    HelmRelease,
    NamedResource,
    strip_resource_attributes,
    STRIP_ATTRIBUTES,
)
from flux_local.orchestrator import BootstrapOptions, Orchestrator, OrchestratorConfig
from flux_local.store import InMemoryStore, Status

from . import selector

_LOGGER = logging.getLogger(__name__)


def build_hr_selector(path: pathlib.Path, **kwargs: Any) -> git_repo.ResourceSelector:
    """Build a HelmRelease selector from CLI arguments."""
    cli_selector = git_repo.ResourceSelector(path=git_repo.PathSelector(path=path))
    cli_selector.helm_release.name = kwargs.get("helmrelease")
    cli_selector.helm_release.namespace = kwargs.get("namespace")
    cli_selector.helm_release.skip_crds = kwargs["skip_crds"]
    cli_selector.helm_release.skip_secrets = kwargs["skip_secrets"]
    cli_selector.helm_release.skip_kinds = kwargs.get("skip_kinds")
    return cli_selector


def filter_manifest(doc: dict[str, Any], **kwargs: Any) -> bool:
    """Return true if the manifest should be included in the output."""
    if kwargs["skip_crds"] and doc.get("kind") == "CustomResourceDefinition":
        return False
    if kwargs["skip_secrets"] and doc.get("kind") == "Secret":
        return False
    if (skip_kinds := kwargs.get("skip_kinds")) and isinstance(skip_kinds, list):
        if doc.get("kind") in skip_kinds:
            return False
    return True


class BuildHelmReleaseAction:
    """Flux-local build for HelmReleases using the new Orchestrator."""

    @classmethod
    def register(
        cls,
        subparsers: SubParsersAction,  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args: ArgumentParser = cast(
            ArgumentParser,
            subparsers.add_parser(
                "helmreleases-new",
                aliases=["hr-new"],
                help="Build HelmRelease objects using the new experimental Orchestrator",
                description=(
                    "The build command uses the new orchestrator to build HelmRelease objects."
                ),
            ),
        )
        args.add_argument(
            "--output-file",
            type=str,
            default="/dev/stdout",
            help="Output file for the results of the command",
        )
        args.add_argument(
            "--wipe-secrets",
            default=True,
            action=BooleanOptionalAction,
            help="Wipe secrets from the output",
        )
        args.add_argument(
            "--enable-oci",
            default=False,
            action=BooleanOptionalAction,
            help="Enable OCI repository sources",
        )
        selector.add_hr_selector_flags(args)
        args.set_defaults(cls=cls)
        return args

    def _process_manifest(
        self, store: InMemoryStore, resource_id: NamedResource, **kwargs: Any
    ) -> list[dict[str, Any]] | None:
        """Process a single HelmRelease and return its manifests if ready."""
        status = store.get_status(resource_id)
        if not status:
            _LOGGER.warning("HelmRelease %s has no status in the store", resource_id)
            return None

        if status.status != Status.READY:
            _LOGGER.error("HelmRelease %s failed: %s", resource_id, status.error)
            return None

        artifact = store.get_artifact(resource_id, HelmReleaseArtifact)
        if not artifact or not artifact.manifests:
            _LOGGER.warning(
                "HelmRelease %s is Ready but has no artifact or manifests",
                resource_id,
            )
            return None

        _LOGGER.info(
            "Found %d manifests for HelmRelease %s",
            len(artifact.manifests),
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
        **kwargs: Any,
    ) -> None:
        """Async Action implementation."""
        _LOGGER.info("Building HelmReleases from path %s using new orchestrator", path)

        store = InMemoryStore()
        config = OrchestratorConfig(enable_helm=True)
        config.kustomization_controller_config.wipe_secrets = kwargs["wipe_secrets"]
        config.read_action_config.wipe_secrets = kwargs["wipe_secrets"]
        config.source_controller_config.enable_oci = kwargs["enable_oci"]
        orchestrator = Orchestrator(store, config)
        bootstrap_options = BootstrapOptions(path=path)
        if not await orchestrator.bootstrap(bootstrap_options):
            _LOGGER.error("Orchestrator bootstrap failed for path %s", path)
            return

        cli_selector = build_hr_selector(path, **kwargs)

        manifest_found = False
        manifest_match = False
        is_match = cli_selector.helm_release.predicate
        with open(output_file, "w", encoding="utf-8") as file:
            for manifest_obj in store.list_objects(kind=HELM_RELEASE):
                if not isinstance(manifest_obj, HelmRelease):
                    continue
                resource_id = NamedResource(
                    kind=manifest_obj.kind,
                    name=manifest_obj.name,
                    namespace=manifest_obj.namespace,
                )

                if not is_match(manifest_obj):
                    _LOGGER.debug("HelmRelease %s did not match selector", resource_id)
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
                        "No HelmReleases found or processed from path %s that matched selector",
                        path,
                    )
                else:
                    _LOGGER.warning(
                        "No HelmReleases that matched the selector were successfully built from path %s",
                        path,
                    )
