"""Flux-local build action."""

from argparse import (
    ArgumentParser,
    _SubParsersAction as SubParsersAction,
    BooleanOptionalAction,
)
import logging
import pathlib
import tempfile
from typing import cast


from flux_local import git_repo

from . import selector
from .visitor import ContentOutput, HelmVisitor


_LOGGER = logging.getLogger(__name__)


class BuildAction:
    """Flux-local build action."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "build",
                help="Build local flux Kustomization target from a local directory",
                description="""You can use the flux-local cli to build all
                    objects in a cluster, similar to how you use kustomize build.
                    This uses kustomize build internally.""",
            ),
        )
        args.add_argument(
            "path", type=pathlib.Path, help="Path to the kustomization or charts"
        )
        args.add_argument(
            "--enable-helm",
            type=bool,
            action=BooleanOptionalAction,
            help="Enable use of HelmRelease inflation",
        )
        args.add_argument(
            "--output-file",
            type=str,
            default="/dev/stdout",
            help="Output file for the results of the command",
        )
        # pylint: disable=duplicate-code
        selector.add_common_flags(args)
        selector.add_helm_options_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        enable_helm: bool,
        skip_crds: bool,
        skip_secrets: bool,
        output_file: str,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""

        query = git_repo.ResourceSelector(path=git_repo.PathSelector(path=path))
        query.kustomization.namespace = None
        query.kustomization.skip_crds = skip_crds
        query.kustomization.skip_secrets = skip_secrets
        query.helm_release.enabled = enable_helm
        query.helm_release.namespace = None
        helm_options = selector.build_helm_options(
            skip_crds=skip_crds, skip_secrets=skip_secrets, **kwargs
        )

        content = ContentOutput()
        query.kustomization.visitor = content.visitor()
        helm_visitor = HelmVisitor()
        query.helm_repo.visitor = helm_visitor.repo_visitor()
        query.helm_release.visitor = helm_visitor.release_visitor()
        await git_repo.build_manifest(
            selector=query, options=selector.options(**kwargs)
        )

        # We use a separate output object so that the contents of the HelmRelease
        # always come after the HelmRelease itself. This means all the helm releases
        # are built at the end. It might be more natural to sort by Kustomization
        # or have the contents of the release immediately following it if we could
        # make the ResourceKeys sort that way, but the helm visitor loses the
        # Kustomziation information at the moment.
        helm_content = ContentOutput()
        if enable_helm:
            with tempfile.TemporaryDirectory() as helm_cache_dir:
                await helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    helm_content.visitor(),
                    helm_options,
                )

        with open(output_file, "w") as file:
            keys = list(content.content)
            keys.sort()
            for key in keys:
                for line in content.content[key]:
                    print(line, file=file)

            keys = list(helm_content.content)
            keys.sort()
            for key in keys:
                for line in helm_content.content[key]:
                    print(line, file=file)
