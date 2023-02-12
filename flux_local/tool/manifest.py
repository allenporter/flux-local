"""Flux-local manifest action."""

import pathlib

from flux_local import git_repo


class ManifestAction:
    """Flux-local manifest action."""

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        manifest = await git_repo.build_manifest(path)
        print(manifest.yaml())
