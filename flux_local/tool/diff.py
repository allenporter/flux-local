"""Flux-local diff action."""

import asyncio
from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
import difflib
import logging
import pathlib
import tempfile
from typing import cast, Generator

import yaml

import git
from flux_local import git_repo
from flux_local.helm import Helm
from flux_local.manifest import HelmRelease, Kustomization, HelmRepository

from . import selector

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


class ResourceContentOutput:
    """Helper object for implementing a git_repo.ResourceVisitor that saves content.

    This effectively binds the resource name to the content for later
    inspection by name.
    """

    def __init__(self) -> None:
        """Initialize KustomizationContentOutput."""
        self.content: dict[str, list[str]] = {}

    def visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""
        return git_repo.ResourceVisitor(content=True, func=self.call)

    def call(
        self, path: pathlib.Path, doc: Kustomization | HelmRelease, content: str | None
    ) -> None:
        """Visitor function invoked to record build output."""
        if content:
            self.content[self.key_func(path, doc)] = (
                content.split("\n") if content else []
            )

    def key_func(
        self, path: pathlib.Path, resource: Kustomization | HelmRelease
    ) -> str:
        return f"{str(path)} - {resource.namespace or 'default'}/{resource.name}"


class HelmVisitor:
    """Helper that visits Helm related objects and handles inflation."""

    def __init__(self) -> None:
        """Initialize KustomizationContentOutput."""
        self.repos: dict[str, list[HelmRepository]] = {}
        self.releases: dict[str, list[HelmRelease]] = {}

    def repo_visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""

        def add_repo(
            path: pathlib.Path, doc: HelmRepository, content: str | None
        ) -> None:
            self.repos[str(path)] = self.repos.get(str(path), []) + [doc]

        return git_repo.ResourceVisitor(content=False, func=add_repo)

    def release_visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""

        def add_release(
            path: pathlib.Path, doc: HelmRelease, content: str | None
        ) -> None:
            self.releases[str(path)] = self.releases.get(str(path), []) + [doc]

        return git_repo.ResourceVisitor(content=False, func=add_release)

    async def inflate(
        self, helm_cache_dir: pathlib.Path, visitor: git_repo.ResourceVisitor
    ) -> None:
        """Expand and notify about HelmReleases discovered."""
        if not visitor.content:
            return

        async def inflate_release(
            cluster_path: pathlib.Path,
            helm: Helm,
            release: HelmRelease,
            visitor: git_repo.ResourceVisitor,
        ) -> None:
            cmd = await helm.template(release, skip_crds=True)
            objs = await cmd.objects()
            content = yaml.dump(objs, explicit_start=True)
            visitor.func(cluster_path, release, content)

        async def inflate_cluster(cluster_path: pathlib.Path) -> None:
            _LOGGER.debug("Inflating Helm charts in cluster %s", cluster_path)
            with tempfile.TemporaryDirectory() as tmp_dir:
                helm = Helm(pathlib.Path(tmp_dir), helm_cache_dir)
                helm.add_repos(self.repos.get(str(cluster_path), []))
                await helm.update()
                tasks = [
                    inflate_release(cluster_path, helm, release, visitor)
                    for release in self.releases.get(str(cluster_path), [])
                ]
                _LOGGER.debug("Waiting for tasks to inflate %s", cluster_path)
                await asyncio.gather(*tasks)

        cluster_paths = set(list(self.releases)) | set(list(self.repos))
        tasks = [
            inflate_cluster(pathlib.Path(cluster_path))
            for cluster_path in cluster_paths
        ]
        _LOGGER.debug("Waiting for cluster inflation to complete")
        await asyncio.gather(*tasks)


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
                helm_visitor.inflate(pathlib.Path(helm_cache_dir), content.visitor()),
                orig_helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir), orig_content.visitor()
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
        path: pathlib.Path,
        enable_helm: bool,
        skip_crds: bool,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        # No-op given subcommands are dispatched
