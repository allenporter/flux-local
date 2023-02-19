"""Flux-local diff action."""

from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
from dataclasses import dataclass
import difflib
import logging
import pathlib
import tempfile
from typing import cast, Any, Generator

from slugify import slugify
from aiofiles.os import mkdir
import yaml

import git
from flux_local import git_repo
from flux_local.helm import Helm
from flux_local.manifest import HelmRelease, Kustomization

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


@dataclass
class HelmReleaseOutput:
    """Holds data related to the output HelmRelease."""

    release: HelmRelease
    content: list[str]

    @property
    def summary(self) -> dict[str, Any]:
        """A short summary of the HelmRelease."""
        return {
            "release": self.release.dict(include={"name", "namespace"}),
            "chart": self.release.chart.dict(),
        }


async def build_helm_release(
    query: git_repo.ResourceSelector, root: pathlib.Path | None = None
) -> HelmReleaseOutput | None:
    """Return the contents of a kustomization object with the specified name."""
    if not root:
        root = query.path.root

    manifest = await git_repo.build_manifest(selector=query)

    with tempfile.TemporaryDirectory() as tmp_dir:
        for cluster in manifest.clusters:
            if not cluster.helm_releases:
                continue

            path = pathlib.Path(tmp_dir) / f"{slugify(cluster.path)}"
            await mkdir(path)

            helm = Helm(path, pathlib.Path(tmp_dir) / "cache")
            helm.add_repos(cluster.helm_repos)
            await helm.update()

            # There should be zero or one HelmRelease
            helm_release = cluster.helm_releases[0]
            _LOGGER.debug(
                "Building HelmRelease for diff: %s/%s",
                helm_release.name,
                helm_release.namespace,
            )
            cmds = await helm.template(helm_release, skip_crds=False)
            objs = await cmds.objects()
            content = yaml.dump(objs, explicit_start=True)
            return HelmReleaseOutput(helm_release, content.split("\n"))
    return None


class ResourceContentOutput:
    """Helper object for implementing a git_repo.ResourceVisitor that saves content.

    This effectively binds the resource name to the content for later
    inspection by name.
    """

    def __init__(self) -> None:
        """Initialize ResourceContentOutput."""
        self.content: dict[str, list[str]] = {}

    def visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""
        return git_repo.ResourceVisitor(content=True, func=self.call)

    def call(self, ks: Kustomization, content: str | None) -> None:
        """Visitor function invoked to record build output."""
        _LOGGER.debug(self.key_func(ks))
        if content:
            _LOGGER.debug(len(content))
            _LOGGER.debug(len(content.split("\n")))
            self.content[self.key_func(ks)] = content.split("\n") if content else []

    def key_func(self, ks: Kustomization) -> str:
        return f"{ks.name} - {ks.path}"


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

        with tempfile.TemporaryDirectory() as tmp_dir:
            await mkdir(pathlib.Path(tmp_dir) / "cache")

        release_output = await build_helm_release(query)
        with git_repo.create_worktree(query.path.repo) as worktree:
            orig_release_output = await build_helm_release(query, worktree)

        if not release_output and not orig_release_output:
            print(selector.not_found("HelmRelease", query.helm_release))
            return

        diff_text = difflib.unified_diff(
            orig_release_output.content if orig_release_output else [],
            release_output.content if release_output else [],
        )
        if output == "diff":
            for line in diff_text:
                print(line)
        elif output == "yaml":
            result = {
                "old": orig_release_output.summary if orig_release_output else None,
                "new": release_output.summary if release_output else None,
                "diff": "\n".join(diff_text),
            }
            print(yaml.dump(result, explicit_start=True))


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
