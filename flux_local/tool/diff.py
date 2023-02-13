"""Flux-local diff action."""

from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
import difflib
import logging
import pathlib
from typing import cast

import git
from flux_local import git_repo

from . import build

_LOGGER = logging.getLogger(__name__)


def changed_files(repo: git.repo.Repo) -> set[str]:
    """Return the set of changed files in the repo."""
    index = repo.index
    return set(
        {diff.a_path for diff in index.diff("HEAD")}
        | {diff.b_path for diff in index.diff("HEAD")}
        | {diff.a_path for diff in index.diff(None)}
        | {diff.b_path for diff in index.diff(None)}
        | {*repo.untracked_files}
    )


async def build_kustomization(
    name: str, root: pathlib.Path, path: pathlib.Path
) -> list[str] | None:
    """Return the contents of a kustomization object with the specified name."""
    selector = git_repo.ResourceSelector(kustomizations={name})
    manifest = await git_repo.build_manifest(path, selector)
    # XXX: Skip crds is a flag
    content_list = []
    for cluster in manifest.clusters:
        for kustomization in cluster.kustomizations:
            _LOGGER.debug(
                "Building Kustomization for diff: %s", root / kustomization.path
            )
            async for content in build.build(
                root / kustomization.path,
                enable_helm=False,
                skip_crds=False,
            ):
                content_list.extend(content.split("\n"))
    return content_list


class DiffKustomizationAction:
    """Flux-local diff for Kustomizations."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args: ArgumentParser = cast(
            ArgumentParser,
            subparsers.add_parser(
                "kustomizations",
                aliases=["ks", "kustomization"],
                help="Diff Kustomization objects",
                description=(
                    "The diff command does a local kustomize build compared "
                    "with the repo and prints the diff."
                ),
            ),
        )
        args.add_argument(
            "kustomization",
            help="The name of the flux Kustomization",
            type=str,
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
        kustomization: str,
        path: pathlib.Path,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        repo = git_repo.git_repo(path)
        content = await build_kustomization(
            kustomization, git_repo.repo_root(repo), path
        )
        with git_repo.create_worktree(repo) as worktree:
            orig_content = await build_kustomization(kustomization, worktree, path)
        if not content and not orig_content:
            print(f"Kustomization '{kustomization}' not found in cluster")
            return

        diff_text = difflib.unified_diff(
            orig_content or [],
            content or [],
        )
        for line in diff_text:
            print(line)


class DiffAction:
    """Flux-local diff action."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args: ArgumentParser = subparsers.add_parser(
            "diff",
            help="Diff a local flux resource",
        )
        subcmds = args.add_subparsers(
            title="Available commands",
            required=True,
        )
        DiffKustomizationAction.register(subcmds)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        enable_helm: bool,
        skip_crds: bool,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        # No-op given subcommands are dispatched
