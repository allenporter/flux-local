"""Flux-local build action."""

import logging
import pathlib
import tempfile


from flux_local import git_repo

from .visitor import ResourceContentOutput, HelmVisitor


_LOGGER = logging.getLogger(__name__)


class BuildAction:
    """Flux-local build action."""

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        enable_helm: bool,
        skip_crds: bool,
        skip_secrets: bool,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""

        query = git_repo.ResourceSelector(path=git_repo.PathSelector(path=path))
        query.kustomization.namespace = None
        query.kustomization.skip_crds = skip_crds
        query.helm_release.enabled = enable_helm
        query.helm_release.namespace = None

        content = ResourceContentOutput()
        query.kustomization.visitor = content.visitor()
        helm_visitor = HelmVisitor()
        query.helm_repo.visitor = helm_visitor.repo_visitor()
        query.helm_release.visitor = helm_visitor.release_visitor()
        await git_repo.build_manifest(selector=query)

        if enable_helm:
            with tempfile.TemporaryDirectory() as helm_cache_dir:
                await helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    content.visitor(),
                    skip_crds,
                    skip_secrets,
                )

        keys = list(content.content)
        keys.sort()
        for key in keys:
            for line in content.content[key]:
                print(line)
