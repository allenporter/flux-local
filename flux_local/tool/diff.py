"""Flux-local diff action."""

from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
from dataclasses import dataclass
import difflib
import logging
import pathlib
import tempfile
from typing import cast, Any

from slugify import slugify
from aiofiles.os import mkdir
import yaml

import git
from flux_local import git_repo
from flux_local.helm import Helm
from flux_local.manifest import HelmRelease

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
    name: str, namespace: str, root: pathlib.Path, path: pathlib.Path
) -> HelmReleaseOutput | None:
    """Return the contents of a kustomization object with the specified name."""
    selector = git_repo.ResourceSelector(helm_release_namespace={namespace})
    manifest = await git_repo.build_manifest(path, selector)

    with tempfile.TemporaryDirectory() as tmp_dir:
        for cluster in manifest.clusters:
            path = pathlib.Path(tmp_dir) / f"{slugify(cluster.path)}"
            await mkdir(path)

            helm = Helm(path, pathlib.Path(tmp_dir) / "cache")
            helm.add_repos(cluster.helm_repos)
            await helm.update()

            for kustomization in cluster.kustomizations:
                if not kustomization.helm_releases:
                    continue
                _LOGGER.debug("Building HelmRelease for diff: %s/%s", name, namespace)
                for helm_release in kustomization.helm_releases:
                    if helm_release.name != name or helm_release.namespace != namespace:
                        continue
                    cmds = await helm.template(helm_release, skip_crds=False)
                    objs = await cmds.objects()
                    content = yaml.dump(objs, explicit_start=True)
                    return HelmReleaseOutput(helm_release, content.split("\n"))
    return None


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
            "--path",
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
        args.add_argument(
            "helmrelease",
            help="The name of the flux HelmRelease",
            type=str,
        )
        args.add_argument(
            "--path",
            help="Optional path with flux Kustomization resources (multi-cluster ok)",
            type=pathlib.Path,
            default=".",
            nargs="?",
        )
        args.add_argument(
            "--namespace",
            "-n",
            type=str,
            help="If present, the namespace scope for this operation",
        )
        args.add_argument(
            "--output",
            "-o",
            choices=["diff", "yaml"],
            default="diff",
            help="If present, the namespace scope for this operation",
        )
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        helmrelease: str,
        path: pathlib.Path,
        namespace: str,
        output: str,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        repo = git_repo.git_repo(path)

        with tempfile.TemporaryDirectory() as tmp_dir:
            await mkdir(pathlib.Path(tmp_dir) / "cache")

        release_output = await build_helm_release(
            helmrelease, namespace, git_repo.repo_root(repo), path
        )
        with git_repo.create_worktree(repo) as worktree:
            orig_release_output = await build_helm_release(
                helmrelease, namespace, worktree, path
            )
        if not release_output and not orig_release_output:
            print(f"HelmRelease '{helmrelease}' not found in cluster")
            return

        diff_text = difflib.unified_diff(
            orig_release_output.content if orig_release_output else [],
            release_output.content if release_output else [],
        )
        print(output)
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
