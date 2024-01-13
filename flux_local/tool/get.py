"""Flux-local get action."""

import logging
from argparse import (
    ArgumentParser,
    BooleanOptionalAction,
    _SubParsersAction as SubParsersAction,
)
from typing import cast, Any
import sys
import pathlib
import tempfile

from flux_local import git_repo, image, helm

from .format import PrintFormatter, YamlFormatter
from . import selector
from .visitor import HelmVisitor, ImageOutput


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
        args.add_argument(
            "--output",
            "-o",
            choices=["wide"],
            default=None,
            help="Output format of the command",
        )
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        output: str | None,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_ks_selector(**kwargs)
        if output != "wide":
            query.helm_release.enabled = False
            query.helm_repo.enabled = False
        manifest = await git_repo.build_manifest(
            selector=query, options=selector.options(**kwargs)
        )

        results: list[dict[str, str]] = []
        cols = ["name", "path"]
        if output == "wide":
            cols.extend(["helmrepos", "releases"])
        if query.kustomization.namespace is None:
            cols.insert(0, "namespace")
        if len(manifest.clusters) > 1:
            cols.insert(0, "cluster")
        for cluster in manifest.clusters:
            for ks in cluster.kustomizations:
                value = ks.dict(include=set(cols))
                if output == "wide":
                    value["helmrepos"] = len(ks.helm_repos)
                    value["releases"] = len(ks.helm_releases)
                value["cluster"] = cluster.path
                results.append(value)

        if not results:
            print(selector.not_found("Kustomization", query.kustomization))
            return

        PrintFormatter(cols).print(results)


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
        manifest = await git_repo.build_manifest(
            selector=query, options=selector.options(**kwargs)
        )

        cols = ["name", "revision", "chart", "source"]
        if query.helm_release.namespace is None:
            cols.insert(0, "namespace")
        results: list[dict[str, Any]] = []
        for cluster in manifest.clusters:
            for helmrelease in cluster.helm_releases:
                value = helmrelease.dict(include=set(cols))
                value["revision"] = str(helmrelease.chart.version)
                value["chart"] = f"{helmrelease.namespace}-{helmrelease.chart.name}"
                value["source"] = helmrelease.chart.repo_name
                results.append(value)

        if not results:
            print(selector.not_found("HelmRelease", query.helm_release))
            return

        PrintFormatter(cols).print(results)


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
            "--enable-images",
            type=str,
            default=False,
            action=BooleanOptionalAction,
            help="Output container images when traversing the cluster",
        )
        args.add_argument(
            "--output",
            "-o",
            choices=["diff", "yaml"],
            default="diff",
            help="Output format of the command",
        )
        args.add_argument(
            "--output-file",
            type=str,
            default="/dev/stdout",
            help="Output file for the results of the command",
        )
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        output: str,
        output_file: str,
        enable_images: bool,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_cluster_selector(**kwargs)
        query.helm_release.enabled = output == "yaml"

        image_visitor: image.ImageVisitor | None = None
        helm_content: ImageOutput | None = None
        if enable_images:
            if output != "yaml":
                print(
                    "Flag --enable-images only works with --output yaml",
                    file=sys.stderr,
                )
                return
            image_visitor = image.ImageVisitor()
            query.doc_visitor = image_visitor.repo_visitor()

            helm_content = ImageOutput()
            helm_visitor = HelmVisitor()
            query.helm_repo.visitor = helm_visitor.repo_visitor()
            query.helm_release.visitor = helm_visitor.release_visitor()

        manifest = await git_repo.build_manifest(
            selector=query, options=selector.options(**kwargs)
        )
        if output == "yaml":
            if image_visitor:
                image_visitor.update_manifest(manifest)
            if helm_content:
                with tempfile.TemporaryDirectory() as helm_cache_dir:
                    await helm_visitor.inflate(
                        pathlib.Path(helm_cache_dir),
                        helm_content.visitor(),
                        helm.Options(),
                    )
                    helm_content.update_manifest(manifest)

            with open(output_file, "w") as file:
                YamlFormatter().print([manifest.compact_dict()], file=file)
            return

        cols = ["path", "kustomizations"]
        results: list[dict[str, Any]] = []
        for cluster in manifest.clusters:
            value: dict[str, Any] = cluster.dict(include=set(cols))
            value["kustomizations"] = len(cluster.kustomizations)
            results.append(value)

        with open(output_file, "w") as file:
            if not results:
                print(
                    selector.not_found("flux cluster Kustmization", query.cluster),
                    file=file,
                )
                return

            PrintFormatter(cols).print(results, file=file)


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
