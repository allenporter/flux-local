"""Flux-local get kustomization action."""

import logging
import pathlib
from argparse import (
    ArgumentParser,
    _SubParsersAction as SubParsersAction,
)
from typing import Any, cast
from collections import Counter

from flux_local import git_repo
from flux_local.kustomize_controller.artifact import KustomizationArtifact
from flux_local.manifest import (
    KUSTOMIZE_KIND,
    Kustomization,
    NamedResource,
)

from .format import PrintFormatter
from . import selector, get_common


_LOGGER = logging.getLogger(__name__)


class GetKustomizationNewAction:
    """Get details about kustomizations using the new Orchestrator."""

    @classmethod
    def register(
        cls,
        subparsers: SubParsersAction,  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "kustomizations-new",
                aliases=["ks-new"],
                help="Get Kustomization objects using the new experimental Orchestrator",
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
        get_common.add_common_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(
        self,
        path: pathlib.Path | None,
        output: str | None,
        **kwargs: Any,
    ) -> None:
        """Async Action implementation."""
        if path is None:
            path = pathlib.Path(".")

        store = await get_common.bootstrap(
            path, enable_oci=kwargs.get("enable_oci", False)
        )

        # We need to build the selector to filter results
        # Reusing logic from build_kustomization.py
        cli_selector = git_repo.ResourceSelector(path=git_repo.PathSelector(path=path))
        cli_selector.kustomization.name = kwargs.get("kustomization")
        cli_selector.kustomization.namespace = kwargs.get("namespace")
        if kwargs.get("all_namespaces"):
            cli_selector.kustomization.namespace = None
        cli_selector.kustomization.skip_crds = kwargs.get("skip_crds", False)
        cli_selector.kustomization.skip_secrets = kwargs.get("skip_secrets", False)
        cli_selector.kustomization.skip_kinds = kwargs.get("skip_kinds")

        results: list[dict[str, Any]] = []
        cols = ["name", "path"]
        if output == "wide":
            cols.extend(["helmrepos", "ocirepos", "releases"])
        if cli_selector.kustomization.namespace is None:
            cols.insert(0, "namespace")

        for manifest_obj in store.list_objects(kind=KUSTOMIZE_KIND):
            if not isinstance(manifest_obj, Kustomization):
                continue

            # Filter
            if not cli_selector.kustomization.predicate(manifest_obj):
                continue

            resource_id = NamedResource(
                kind=manifest_obj.kind,
                name=manifest_obj.name,
                namespace=manifest_obj.namespace,
            )

            value: dict[str, str | int | None] = {
                "name": manifest_obj.name,
                "namespace": manifest_obj.namespace,
                "path": manifest_obj.path,
            }

            if output == "wide":
                artifact = store.get_artifact(resource_id, KustomizationArtifact)
                manifests = artifact.manifests if artifact else []
                counts: Counter[str] = Counter(doc["kind"] for doc in manifests)
                value["helmrepos"] = counts["HelmRepository"]
                value["ocirepos"] = counts["OCIRepository"]
                value["releases"] = counts["HelmRelease"]

            results.append(value)

        if not results:
            print(selector.not_found("Kustomization", cli_selector.kustomization))
            return

        PrintFormatter(cols).print(results)
