"""Flux-local diff action."""

import asyncio
import os
from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
from contextlib import contextmanager
from dataclasses import asdict
import difflib
import logging
import pathlib
import shlex
import tempfile
from typing import cast, Generator, Any, AsyncGenerator
import yaml


from flux_local import git_repo, command

from . import selector
from .visitor import HelmVisitor, ObjectOutput, ResourceKey

_LOGGER = logging.getLogger(__name__)


def perform_object_diff(
    a: ObjectOutput,
    b: ObjectOutput,
    n: int,
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""
    for kustomization_key in set(a.content.keys()) | set(b.content.keys()):
        _LOGGER.debug(
            "Diffing results for Kustomization %s (n=%d)", kustomization_key, n
        )
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in set(a_resources.keys()) | set(b_resources.keys()):
            diff_text = difflib.unified_diff(
                a=a_resources.get(resource_key, []),
                b=b_resources.get(resource_key, []),
                fromfile=f"{kustomization_key.label} {resource_key.compact_label}",
                tofile=f"{kustomization_key.label} {resource_key.compact_label}",
                n=n,
            )
            for line in diff_text:
                yield line


async def perform_external_diff(
    cmd: list[str],
    a: ObjectOutput,
    b: ObjectOutput,
) -> AsyncGenerator[str, None]:
    """Generate diffs between the two output objects."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for kustomization_key in set(a.content.keys()) | set(b.content.keys()):
            _LOGGER.debug(
                "Diffing results for Kustomization %s",
                kustomization_key,
            )
            a_resources = a.content.get(kustomization_key, {})
            b_resources = b.content.get(kustomization_key, {})
            keys = set(a_resources.keys()) | set(b_resources.keys())

            a_file = pathlib.Path(tmpdir) / "a.yaml"
            a_file.write_text(
                "\n".join(
                    [
                        "\n".join(a_resources.get(resource_key, []))
                        for resource_key in keys
                    ]
                )
            )
            b_file = pathlib.Path(tmpdir) / "b.yaml"
            b_file.write_text(
                "\n".join(
                    [
                        "\n".join(b_resources.get(resource_key, []))
                        for resource_key in keys
                    ]
                )
            )

            out = await command.Command(
                cmd + [str(a_file), str(b_file)], retcodes=[0, 1]
            ).run()
            if out:
                yield out.decode("utf-8")


def omit_none(obj: Any) -> dict[str, Any]:
    """Creates a dictionary with None values missing."""
    return {k: v for k, v in obj if v is not None}


def perform_yaml_diff(
    a: ObjectOutput,
    b: ObjectOutput,
    n: int,
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""

    diffs = []
    for kustomization_key in set(a.content.keys()) | set(b.content.keys()):
        _LOGGER.debug("Diffing results for %s (n=%d)", kustomization_key, n)
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        resource_diffs = []
        for resource_key in set(a_resources.keys()) | set(b_resources.keys()):
            diff_text = difflib.unified_diff(
                a=a_resources.get(resource_key, []),
                b=b_resources.get(resource_key, []),
                fromfile=f"{kustomization_key.label} {resource_key.compact_label}",
                tofile=f"{kustomization_key.label} {resource_key.compact_label}",
                n=n,
            )
            diff_content = "\n".join(diff_text)
            if not diff_content:
                continue
            obj = {
                **asdict(resource_key, dict_factory=omit_none),
                "diff": diff_content,
            }
            resource_diffs.append(obj)
        if resource_diffs:
            diffs.append(
                {
                    **asdict(kustomization_key),
                    "diffs": resource_diffs,
                }
            )
    if diffs:
        yield yaml.dump(diffs, sort_keys=False, explicit_start=True, default_style=None)


def get_helm_release_diff_keys(
    a: ObjectOutput, b: ObjectOutput
) -> dict[str, list[ResourceKey]]:
    """Return HelmRelease resource keys with diffs, by cluster."""
    result: dict[str, list[ResourceKey]] = {}
    for kustomization_key in set(a.content.keys()) | set(b.content.keys()):
        cluster_path = kustomization_key.cluster_path
        _LOGGER.debug("Diffing results for Kustomization %s", kustomization_key)
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in set(a_resources.keys()) | set(b_resources.keys()):
            if resource_key.kind != "HelmRelease":
                continue
            if a_resources.get(resource_key) != b_resources.get(resource_key):
                result[cluster_path] = result.get(cluster_path, []) + [resource_key]
    return result


def add_diff_flags(args: ArgumentParser) -> None:
    """Add shared diff flags."""
    args.add_argument(
        "--output",
        "-o",
        choices=["diff", "yaml", "object"],
        default="diff",
        help="Output format of the command",
    )
    args.add_argument(
        "--unified",
        "-u",
        type=int,
        default=3,
        help="output NUM (default 3) lines of unified context",
    )
    args.add_argument(
        "--path-orig",
        help="Path to compare against, or creates a work tree if not specified",
        type=pathlib.Path,
        default=None,
        nargs="?",
    )


@contextmanager
def create_diff_path(
    selector: git_repo.PathSelector,
    **kwargs: Any,
) -> Generator[git_repo.PathSelector, None, None]:
    """Create a context manager for the diff path.

    This will create a new worktree by default, or use the path in the flags
    which is useful when run from CI.
    """
    if path_orig := kwargs.get("path_orig"):
        yield git_repo.PathSelector(path_orig)
        return

    with git_repo.create_worktree(selector.repo) as worktree:
        yield git_repo.PathSelector(pathlib.Path(worktree) / selector.relative_path)


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
        add_diff_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        output: str,
        unified: int,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_ks_selector(**kwargs)
        query.helm_release.enabled = False

        content = ObjectOutput()
        query.kustomization.visitor = content.visitor()
        await git_repo.build_manifest(selector=query)

        orig_content = ObjectOutput()
        with create_diff_path(query.path, **kwargs) as path_selector:
            query.path = path_selector
            query.kustomization.visitor = orig_content.visitor()
            await git_repo.build_manifest(selector=query)

        if not orig_content.content and not content.content:
            print(selector.not_found("Kustomization", query.kustomization))
            return

        _LOGGER.debug("Diffing content")
        if output == "yaml":
            result = perform_yaml_diff(orig_content, content, unified)
            for line in result:
                print(line)
        elif external_diff := os.environ.get("DIFF"):
            async for line in perform_external_diff(
                shlex.split(external_diff), orig_content, content
            ):
                print(line)
        else:
            result = perform_object_diff(orig_content, content, unified)
            for line in result:
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
        add_diff_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        output: str,
        unified: int,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_hr_selector(**kwargs)
        content = ObjectOutput()
        helm_visitor = HelmVisitor()
        query.kustomization.visitor = content.visitor()
        query.helm_repo.visitor = helm_visitor.repo_visitor()
        query.helm_release.visitor = helm_visitor.release_visitor()
        await git_repo.build_manifest(selector=query)

        orig_content = ObjectOutput()
        orig_helm_visitor = HelmVisitor()
        with create_diff_path(query.path, **kwargs) as path_selector:
            query.path = path_selector
            query.kustomization.visitor = orig_content.visitor()
            query.helm_repo.visitor = orig_helm_visitor.repo_visitor()
            query.helm_release.visitor = orig_helm_visitor.release_visitor()
            await git_repo.build_manifest(selector=query)

        if not helm_visitor.releases and not orig_helm_visitor.releases:
            print(selector.not_found("HelmRelease", query.helm_release))
            return

        # Find HelmRelease objects with diffs and prune all other HelmReleases from
        # the helm visitors. We assume that the only way for a HelmRelease output
        # to have a diff is if the HelmRelease in the kustomization has a diff.
        # This avoid building unnecessary resources and churn from things like
        # random secret generation.
        diff_resource_keys = get_helm_release_diff_keys(orig_content, content)
        cluster_paths = {
            kustomization_key.cluster_path
            for kustomization_key in set(orig_content.content.keys())
            | set(content.content.keys())
        }
        for cluster_path in cluster_paths:
            diff_keys = diff_resource_keys.get(cluster_path, [])
            diff_names = {
                f"{resource_key.namespace}/{resource_key.name}"
                for resource_key in diff_keys
            }
            if cluster_path in helm_visitor.releases:
                releases = [
                    release
                    for release in helm_visitor.releases[cluster_path]
                    if f"{release.namespace}/{release.name}" in diff_names
                ]
                helm_visitor.releases[cluster_path] = releases
            if cluster_path in orig_helm_visitor.releases:
                releases = [
                    release
                    for release in orig_helm_visitor.releases[cluster_path]
                    if f"{release.namespace}/{release.name}" in diff_names
                ]
                orig_helm_visitor.releases[cluster_path] = releases

        helm_content = ObjectOutput()
        orig_helm_content = ObjectOutput()
        with tempfile.TemporaryDirectory() as helm_cache_dir:
            await asyncio.gather(
                helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    helm_content.visitor(),
                    query.helm_release.skip_crds,
                    skip_secrets=query.helm_release.skip_secrets,
                ),
                orig_helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    orig_helm_content.visitor(),
                    query.helm_release.skip_crds,
                    skip_secrets=query.helm_release.skip_secrets,
                ),
            )

        if output == "yaml":
            for line in perform_yaml_diff(orig_helm_content, helm_content, unified):
                print(line)
        elif external_diff := os.environ.get("DIFF"):
            async for line in perform_external_diff(
                shlex.split(external_diff), orig_helm_content, helm_content
            ):
                print(line)
        else:
            for line in perform_object_diff(orig_helm_content, helm_content, unified):
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
