"""Flux-local build action."""

import logging
import pathlib
import tempfile
from typing import AsyncGenerator

import yaml
from aiofiles.os import mkdir
from slugify import slugify

from flux_local import git_repo, kustomize
from flux_local.helm import Helm
from flux_local.manifest import Kustomization

_LOGGER = logging.getLogger(__name__)


CRD_RESOURCE = "CustomResourceDefinition"


async def build_kustomization(
    root: pathlib.Path,
    kustomization: Kustomization,
    helm: Helm | None,
    skip_crds: bool,
) -> AsyncGenerator[str, None]:
    """Flux-local build action for a kustomization."""
    cmds = kustomize.build(root / kustomization.path)
    if skip_crds:
        cmds = cmds.grep(f"kind=^{CRD_RESOURCE}$", invert=True)
    if helm:
        # Exclude HelmReleases and expand below
        cmds = cmds.grep_helm_release(invert=True)
    objs = await cmds.objects()
    yield yaml.dump(objs)

    if not helm:
        return

    for helm_release in kustomization.helm_releases:
        cmds = await helm.template(helm_release, skip_crds=skip_crds)
        objs = await cmds.objects()
        print(objs)
        yield yaml.dump(objs)


async def build(
    path: pathlib.Path, enable_helm: bool, skip_crds: bool
) -> AsyncGenerator[str, None]:
    """Flux-local build action."""
    query = git_repo.ResourceSelector()
    query.path = git_repo.PathSelector(path)
    query.kustomization.namespace = None
    query.helm_release.namespace = None
    manifest = await git_repo.build_manifest(selector=query)

    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_path = pathlib.Path(tmp_dir) / "cache"
        await mkdir(cache_path)

        for cluster in manifest.clusters:
            helm = None
            if enable_helm:
                helm_path = (
                    pathlib.Path(tmp_dir) / f"{slugify(cluster.path or 'cluster')}"
                )
                await mkdir(helm_path)

                helm = Helm(helm_path, cache_path)
                helm.add_repos(cluster.helm_repos)
                await helm.update()

            for kustomization in cluster.kustomizations:
                async for content in build_kustomization(
                    query.path.root, kustomization, helm, skip_crds
                ):
                    yield content


class BuildAction:
    """Flux-local build action."""

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        enable_helm: bool,
        skip_crds: bool,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""

        async for content in build(path, enable_helm, skip_crds):
            print(content)
