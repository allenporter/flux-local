"""Flux-local build action."""

import logging
import pathlib
import tempfile


from flux_local import git_repo

from . import selector
from .visitor import ContentOutput, HelmVisitor


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

        keys = list(content.content)
        keys.sort()
        for key in keys:
            for line in content.content[key]:
                print(line)

        keys = list(helm_content.content)
        keys.sort()
        for key in keys:
            for line in helm_content.content[key]:
                print(line)
