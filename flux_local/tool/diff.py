"""Flux-local diff action."""

import asyncio
from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
import difflib
import logging
import pathlib
import tempfile
from typing import cast, Generator


import git
from flux_local import git_repo

from . import selector
from .visitor import ResourceContentOutput, HelmVisitor

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


def perform_diff(
    a: ResourceContentOutput, b: ResourceContentOutput
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""
    for key in set(a.content.keys()) | set(b.content.keys()):
        _LOGGER.debug("Diffing results for %s", key)
        diff_text = difflib.unified_diff(
            a=a.content.get(key) or [],
            b=b.content.get(key) or [],
            fromfile=key,
            tofile=key,
        )
        for line in diff_text:
            yield line


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
        selector.add_ks_selector_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_ks_selector(**kwargs)
        query.helm_release.enabled = False

        content = ResourceContentOutput()
        query.kustomization.visitor = content.visitor()
        await git_repo.build_manifest(selector=query)

        orig_content = ResourceContentOutput()
        with git_repo.create_worktree(query.path.repo) as worktree:
            relative_path = query.path.relative_path
            query.path = git_repo.PathSelector(pathlib.Path(worktree) / relative_path)
            query.kustomization.visitor = orig_content.visitor()
            await git_repo.build_manifest(selector=query)

        if not orig_content.content and not content.content:
            print(selector.not_found("Kustomization", query.kustomization))
            return

        _LOGGER.debug("Diffing content")
        for line in perform_diff(orig_content, content):
            print(line)


class DiffHelmReleaseAction:
    """Flux-local diff for HelmRelease."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args: ArgumentParser = cast(
            ArgumentParser,
            subparsers.add_parser(
                "helmreleases",
                aliases=["hr", "helmrelease"],
                help="Diff HelmRelease objects",
                description=(
                    "The diff command does a local kustomize build, then inflates "
                    "the helm template and compares with the repo and prints the diff."
                ),
            ),
        )
        selector.add_hr_selector_flags(args)
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
        output: str,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_hr_selector(**kwargs)
        helm_visitor = HelmVisitor()
        query.helm_repo.visitor = helm_visitor.repo_visitor()
        query.helm_release.visitor = helm_visitor.release_visitor()
        await git_repo.build_manifest(selector=query)

        orig_helm_visitor = HelmVisitor()
        with git_repo.create_worktree(query.path.repo) as worktree:
            relative_path = query.path.relative_path
            query.path = git_repo.PathSelector(pathlib.Path(worktree) / relative_path)
            query.helm_repo.visitor = orig_helm_visitor.repo_visitor()
            query.helm_release.visitor = orig_helm_visitor.release_visitor()
            await git_repo.build_manifest(selector=query)

        if not helm_visitor.releases and not orig_helm_visitor.releases:
            print(selector.not_found("HelmRelease", query.helm_release))
            return

        content = ResourceContentOutput()
        orig_content = ResourceContentOutput()
        with tempfile.TemporaryDirectory() as helm_cache_dir:
            await asyncio.gather(
                helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    content.visitor(),
                    query.helm_release.skip_crds,
                ),
                orig_helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    orig_content.visitor(),
                    query.helm_release.skip_crds,
                ),
            )

        for line in perform_diff(orig_content, content):
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
        DiffHelmReleaseAction.register(subcmds)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        # No-op given subcommands are dispatched
