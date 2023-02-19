"""Flux-local get action."""

import logging
from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
from typing import cast

from flux_local import git_repo

from .format import print_columns
from . import selector


_LOGGER = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "flux-system"


class GetKustomizationAction:
    """Get details about kustomizations."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "kustomizations",
                aliases=["ks", "kustomization"],
                help="Get Kustomization objects",
                description="Print information about local flux Kustomization objects",
            ),
        )
        selector.add_ks_selector_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_ks_selector(**kwargs)
        manifest = await git_repo.build_manifest(selector=query)
        cols = ["NAME", "PATH", "HELMREPOS", "RELEASES"]
        if len(manifest.clusters) > 1:
            cols.insert(0, "CLUSTER")
        results: list[list[str]] = []
        for cluster in manifest.clusters:
            for ks in cluster.kustomizations:
                value = [
                    ks.name,
                    ks.path,
                    str(len(ks.helm_repos)),
                    str(len(ks.helm_releases)),
                ]
                if len(manifest.clusters) > 1:
                    value.insert(0, cluster.path)
                results.append(value)
        if not results:
            print(selector.not_found("Kustomization", query.kustomization))
            return

        print_columns(cols, results)


class GetHelmReleaseAction:
    """Get details about HelmReleases."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "helmreleases",
                aliases=["hr", "helmrelease"],
                help="Get HelmRelease objects",
                description="Print information about local flux HelmRelease objects",
            ),
        )
        selector.add_hr_selector_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_hr_selector(**kwargs)
        manifest = await git_repo.build_manifest(selector=query)
        cols = ["NAME", "REVISION", "CHART", "SOURCE"]
        if query.helm_release.namespace is None:
            cols.insert(0, "NAMESPACE")
        results: list[list[str]] = []
        for cluster in manifest.clusters:
            for helmrelease in cluster.helm_releases:
                value: list[str] = [
                    helmrelease.name,
                    str(helmrelease.chart.version),
                    f"{helmrelease.namespace}-{helmrelease.chart.name}",
                    helmrelease.chart.repo_name,
                ]
                if query.helm_release.namespace is None:
                    value.insert(0, helmrelease.namespace)
                results.append(value)
        if not results:
            print(selector.not_found("HelmRelease", query.helm_release))
            return

        print_columns(cols, results)


class GetClusterAction:
    """Get details about flux clustaers."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "clusters",
                aliases=["cl", "cluster"],
                help="Get get flux cluster definitions",
                description="Print information about local flux cluster definitions",
            ),
        )
        selector.add_cluster_selector_flags(args)
        args.add_argument(
            "--output",
            "-o",
            choices=["diff", "yaml"],
            default="diff",
            help="Output format of the command",
        )
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_cluster_selector(**kwargs)
        query.helm_release.enabled = False
        manifest = await git_repo.build_manifest(selector=query)
        cols = ["NAME", "PATH", "KUSTOMIZATIONS"]
        if query.cluster.namespace is None:
            cols.insert(0, "NAMESPACE")
        results: list[list[str]] = []
        for cluster in manifest.clusters:
            value: list[str] = [
                cluster.name,
                cluster.path,
                str(len(cluster.kustomizations)),
            ]
            if query.cluster.namespace is None:
                value.insert(0, cluster.namespace)
            results.append(value)
        if not results:
            print(selector.not_found("flux cluster Kustmization", query.cluster))
            return

        print_columns(cols, results)


class GetAction:
    """Flux-local get action."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "get",
                help="Print information about local flux resources",
                description="Print information about supported local flux resources",
            ),
        )
        subcmds = args.add_subparsers(
            title="Available commands",
            required=True,
        )
        GetKustomizationAction.register(subcmds)
        GetHelmReleaseAction.register(subcmds)
        GetClusterAction.register(subcmds)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        # No-op given subcommands are always the dispatch target
