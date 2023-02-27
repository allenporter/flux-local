"""Flux-local diff action."""

import asyncio
from argparse import ArgumentParser
from argparse import _SubParsersAction as SubParsersAction
from contextlib import contextmanager
from dataclasses import asdict
import difflib
import logging
import pathlib
import tempfile
from typing import cast, Generator, Any
import yaml


from flux_local import git_repo

from . import selector
from .visitor import HelmVisitor, ObjectOutput

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
                fromfile=f"{kustomization_key.label} {resource_key.label}",
                tofile=f"{kustomization_key.label} {resource_key.label}",
                n=n,
            )
            for line in diff_text:
                yield line


def omit_none(obj: Any) -> dict[str, Any]:
    """Creates a dictionary with None values missing."""
    return {k: v for k, v in obj if v is not None}


def perform_yaml_diff(
    a: ObjectOutput,
    b: ObjectOutput,
    n: int,
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""

    def str_presenter(dumper: yaml.Dumper, data: Any) -> Any:
        """Represent multi-line yaml strings as you'd expect.

        See https://github.com/yaml/pyyaml/issues/240
        """
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style="|" if data.count("\n") > 0 else None
        )

    yaml.add_representer(str, str_presenter)

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
                n=n,
            )
            diff_content = "\n".join(diff_text)
            if not diff_content:
                continue
            obj = {
                **asdict(resource_key, dict_factory=omit_none),
                "diff_text": diff_content,
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
        helm_visitor = HelmVisitor()
        query.helm_repo.visitor = helm_visitor.repo_visitor()
        query.helm_release.visitor = helm_visitor.release_visitor()
        await git_repo.build_manifest(selector=query)

        orig_helm_visitor = HelmVisitor()
        with create_diff_path(query.path, **kwargs) as path_selector:
            query.path = path_selector
            query.helm_repo.visitor = orig_helm_visitor.repo_visitor()
            query.helm_release.visitor = orig_helm_visitor.release_visitor()
            await git_repo.build_manifest(selector=query)

        if not helm_visitor.releases and not orig_helm_visitor.releases:
            print(selector.not_found("HelmRelease", query.helm_release))
            return

        content = ObjectOutput()
        orig_content = ObjectOutput()
        with tempfile.TemporaryDirectory() as helm_cache_dir:
            await asyncio.gather(
                helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    content.visitor(),
                    query.helm_release.skip_crds,
                    skip_secrets=query.helm_release.skip_secrets,
                ),
                orig_helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir),
                    orig_content.visitor(),
                    query.helm_release.skip_crds,
                    skip_secrets=query.helm_release.skip_secrets,
                ),
            )

        if output == "yaml":
            for line in perform_yaml_diff(orig_content, content, unified):
                print(line)
        else:
            for line in perform_object_diff(orig_content, content, unified):
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
