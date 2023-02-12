"""Flux-local get action."""

import logging
import pathlib
from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction

from flux_local import git_repo

from .format import print_columns

_LOGGER = logging.getLogger(__name__)


class GetKustomizationAction:
    """Get details about kustomizations."""

    @classmethod
    def register(cls, subparsers: SubParsersAction[ArgumentParser]) -> ArgumentParser:
        """Register the subparser commands."""
        args = subparsers.add_parser(
            "kustomizations",
            aliases=["ks", "kustomization"],
            help="Get Kustomization objects",
            description="Print information about local flux Kustomization objects",
        )
        args.add_argument(
            "path",
            help="Optional path with flux Kustomization resources (multi-cluster ok)",
            type=pathlib.Path,
            default=".",
            nargs="?",
        )
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        manifest = await git_repo.build_manifest(path)
        cols = ["NAME", "PATH", "HELM_REPOS", "HELM_RELEASES"]
        if len(manifest.clusters) > 1:
            cols.insert(0, "CLUSTER")
        results: list[list[str]] = []
        for cluster in manifest.clusters:
            for kustomization in cluster.kustomizations:
                value = [
                    kustomization.name,
                    kustomization.path,
                    str(len(kustomization.helm_repos)),
                    str(len(kustomization.helm_releases)),
                ]
                if len(manifest.clusters) > 1:
                    value.insert(0, cluster.path)
                results.append(value)
        print_columns(cols, results)


class GetHelmReleaseAction:
    """Get details about HelmReleases."""

    @classmethod
    def register(cls, subparsers: SubParsersAction[ArgumentParser]) -> ArgumentParser:
        """Register the subparser commands."""
        args = subparsers.add_parser(
            "helmreleases",
            aliases=["hr", "helmrelease"],
            help="Get HelmRelease objects",
            description="Print information about local flux HelmRelease objects",
        )
        args.add_argument(
            "path",
            help="Optional path with flux Kustomization resources (multi-cluster ok)",
            type=pathlib.Path,
            default=".",
            nargs="?",
        )
        args.add_argument(
            "--namespace",
            "-n",
            type=str,
            default=None,
            help="If present, the namespace scope for this operation (default ALL)",
        )
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        namespace: str | None,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        manifest = await git_repo.build_manifest(path)
        cols = ["NAME", "REVISION", "CHART", "SOURCE"]
        if namespace is None:
            cols.insert(0, "NAMESPACE")
        results: list[list[str]] = []
        for cluster in manifest.clusters:
            for helmrelease in cluster.helm_releases:
                if namespace is not None and helmrelease.namespace != namespace:
                    continue
                value: list[str] = [
                    helmrelease.name,
                    str(helmrelease.chart.version),
                    f"{helmrelease.namespace}-{helmrelease.chart.name}",
                    helmrelease.chart.repo_name,
                ]
                if namespace is None:
                    value.insert(0, helmrelease.namespace)
                results.append(value)
        print_columns(cols, results)


class GetAction:
    """Flux-local get action."""

    @classmethod
    def register(cls, subparsers: SubParsersAction[ArgumentParser]) -> ArgumentParser:
        """Register the subparser commands."""
        args = subparsers.add_parser(
            "get",
            help="Print information about local flux resources",
            description="Print information about supported local flux resources",
        )
        subcmds = args.add_subparsers(
            title="Available commands",
            required=True,
        )
        GetKustomizationAction.register(subcmds)
        GetHelmReleaseAction.register(subcmds)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        # No-op given subcommands are always the dispatch target
