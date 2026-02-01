"""Flux-local get helmrelease action."""

import logging
import pathlib
from argparse import (
    ArgumentParser,
    _SubParsersAction as SubParsersAction,
)
from typing import Any, cast

from flux_local.kustomize_controller.artifact import KustomizationArtifact
from flux_local.manifest import (
    KUSTOMIZE_KIND,
    Kustomization,
    HelmRelease,
    NamedResource,
)
from flux_local import git_repo

from .format import PrintFormatter
from . import selector, get_common


_LOGGER = logging.getLogger(__name__)


class GetHelmReleaseNewAction:
    """Get details about HelmReleases using the new Orchestrator."""

    @classmethod
    def register(
        cls,
        subparsers: SubParsersAction,  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "helmreleases-new",
                aliases=["hr-new"],
                help="Get HelmRelease objects using the new experimental Orchestrator",
                description="Print information about local flux HelmRelease objects",
            ),
        )
        selector.add_hr_selector_flags(args)
        get_common.add_common_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(
        self,
        path: pathlib.Path | None,
        builder: git_repo.CachableBuilder | None = None,
        **kwargs: Any,
    ) -> None:
        """Async Action implementation."""
        if path is None:
            path = pathlib.Path(".")

        store = await get_common.bootstrap(
            path, enable_oci=kwargs.get("enable_oci", False)
        )

        cli_selector = selector.build_hr_selector(**kwargs)

        results: list[dict[str, Any]] = []
        cols = ["name", "revision", "chart", "source"]
        if cli_selector.helm_release.namespace is None:
            cols.insert(0, "namespace")

        for manifest_obj in store.list_objects(kind=KUSTOMIZE_KIND):
            if not isinstance(manifest_obj, Kustomization):
                continue

            resource_id = NamedResource(
                kind=manifest_obj.kind,
                name=manifest_obj.name,
                namespace=manifest_obj.namespace,
            )

            artifact = store.get_artifact(resource_id, KustomizationArtifact)
            if not artifact:
                continue

            for doc in artifact.manifests:
                if doc.get("kind") != "HelmRelease":
                    continue

                try:
                    helm_release = HelmRelease.parse_doc(doc)
                except Exception as e:
                    _LOGGER.debug("Failed to parse HelmRelease: %s", e)
                    continue

                if not cli_selector.helm_release.predicate(helm_release):
                    continue

                value = {
                    "name": helm_release.name,
                    "namespace": helm_release.namespace,
                    "revision": str(helm_release.chart.version),
                    "chart": f"{helm_release.namespace}-{helm_release.chart.name}",
                    "source": helm_release.chart.repo_name,
                }
                results.append(value)

        if not results:
            print(selector.not_found("HelmRelease", cli_selector.helm_release))
            return

        PrintFormatter(cols).print(results)
