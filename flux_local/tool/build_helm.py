"""Flux-local build for HelmReleases using the new Orchestrator."""

import logging
import pathlib
from argparse import (
    ArgumentParser,
    _SubParsersAction as SubParsersAction,
    BooleanOptionalAction,
)
from typing import Any, cast

from flux_local import git_repo
from flux_local.helm_controller.artifact import HelmReleaseArtifact
from flux_local.manifest import (
    HELM_RELEASE,
    HelmRelease,
)
from flux_local.orchestrator import OrchestratorConfig

from . import selector
from .build_common import BuildRunner

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

    async def run(
        self,
        path: pathlib.Path,
        output_file: str,
        **kwargs: Any,
    ) -> None:
        """Async Action implementation."""
        config = OrchestratorConfig(enable_helm=True)
        config.kustomization_controller_config.wipe_secrets = kwargs["wipe_secrets"]
        config.read_action_config.wipe_secrets = kwargs["wipe_secrets"]
        config.source_controller_config.enable_oci = kwargs["enable_oci"]

        cli_selector = build_hr_selector(path, **kwargs)

        runner = BuildRunner(
            config=config,
            resource_kind=HELM_RELEASE,
            resource_type=HelmRelease,
            artifact_type=HelmReleaseArtifact,
            selector_predicate=cli_selector.helm_release.predicate,  # type: ignore[arg-type]
        )
        await runner.run(path, output_file, **kwargs)
