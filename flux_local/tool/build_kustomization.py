"""Flux-local build for Kustomizations using the new Orchestrator."""

import logging
import pathlib
from argparse import (
    ArgumentParser,
    _SubParsersAction as SubParsersAction,
    BooleanOptionalAction,
)
from typing import Any, cast

from flux_local import git_repo
from flux_local.kustomize_controller.artifact import KustomizationArtifact
from flux_local.manifest import (
    KUSTOMIZE_KIND,
    Kustomization,
)
from flux_local.orchestrator import OrchestratorConfig

from . import selector
from .build_common import BuildRunner

_LOGGER = logging.getLogger(__name__)


def build_ks_selector(path: pathlib.Path, **kwargs: Any) -> git_repo.ResourceSelector:
    """Build a Kustomization selector from CLI arguments.

    We build this rather than using the selecot logic to avoid some assumptions
    made about the path in the old selector logic.
    """
    cli_selector = git_repo.ResourceSelector(path=git_repo.PathSelector(path=path))
    cli_selector.kustomization.name = kwargs.get("name")
    cli_selector.kustomization.namespace = kwargs.get("namespace")
    cli_selector.kustomization.skip_crds = kwargs["skip_crds"]
    cli_selector.kustomization.skip_secrets = kwargs["skip_secrets"]
    cli_selector.kustomization.skip_kinds = kwargs.get("skip_kinds")
    return cli_selector


class BuildKustomizationAction:
    """Flux-local build for Kustomizations using the new Orchestrator."""

    @classmethod
    def register(
        cls,
        subparsers: SubParsersAction,  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args: ArgumentParser = cast(
            ArgumentParser,
            subparsers.add_parser(
                "kustomizations-new",
                aliases=["ks-new"],
                help="Build Kustomization objects using the new experimental Orchestrator",
                description=(
                    "The build command uses the new orchestrator to build Kustomization objects."
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
        selector.add_ks_selector_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(
        self,
        path: pathlib.Path,
        output_file: str,
        **kwargs: Any,
    ) -> None:
        """Async Action implementation."""
        # Disable Helm for ks-only build
        config = OrchestratorConfig(enable_helm=False)
        config.kustomization_controller_config.wipe_secrets = kwargs["wipe_secrets"]
        config.read_action_config.wipe_secrets = kwargs["wipe_secrets"]
        config.source_controller_config.enable_oci = kwargs["enable_oci"]

        cli_selector = build_ks_selector(path, **kwargs)

        runner = BuildRunner(
            config=config,
            resource_kind=KUSTOMIZE_KIND,
            resource_type=Kustomization,
            artifact_type=KustomizationArtifact,
            selector_predicate=cli_selector.kustomization.predicate,  # type: ignore[arg-type]
        )
        await runner.run(path, output_file, **kwargs)
