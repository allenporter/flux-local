"""Command line tool for building, diffing, validating flux local repositories."""

import difflib
import logging
import pathlib

from flux_local import git_repo

from . import build

_LOGGER = logging.getLogger(__name__)


class DiffAction:
    """Flux-local diff action."""

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        enable_helm: bool,
        skip_crds: bool,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        repo = git_repo.git_repo(path)

        content1 = []
        with git_repo.create_worktree(repo) as worktree_dir:
            async for content in build.build(worktree_dir, enable_helm, skip_crds):
                content1.append(content)

        content2 = []
        async for content in build.build(
            git_repo.repo_root(repo), enable_helm, skip_crds
        ):
            content2.append(content)

        diff_text = difflib.unified_diff(
            "".join(content1).split("\n"), "".join(content2).split("\n")
        )
        print("\n".join(diff_text))
