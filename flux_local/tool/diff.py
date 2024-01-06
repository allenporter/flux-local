"""Flux-local diff action."""

import asyncio
import functools
import os
from argparse import ArgumentParser, _SubParsersAction as SubParsersAction
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import asdict
import difflib
import logging
import pathlib
import shlex
import tempfile
from typing import cast, Generator, Any, AsyncGenerator, TypeVar
import yaml


from flux_local import git_repo, command

from . import selector
from .visitor import HelmVisitor, ObjectOutput, ResourceKey

_LOGGER = logging.getLogger(__name__)

# Type for command line flags of comma separated list
_CSV = functools.partial(str.split, sep=",")

_TRUNCATE = "[Diff truncated by flux-local]"

T = TypeVar("T")


def _unique_keys(k1: dict[T, Any], k2: dict[T, Any]) -> Iterable[T]:
    """Return an ordered set."""
    return {
        **{k: True for k in k1.keys()},
        **{k: True for k in k2.keys()},
    }.keys()


def perform_object_diff(
    a: ObjectOutput, b: ObjectOutput, n: int, limit_bytes: int
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""
    for kustomization_key in _unique_keys(a.content, b.content):
        _LOGGER.debug(
            "Diffing results for Kustomization %s (n=%d)", kustomization_key, n
        )
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in _unique_keys(a_resources, b_resources):
            diff_text = difflib.unified_diff(
                a=a_resources.get(resource_key, []),
                b=b_resources.get(resource_key, []),
                fromfile=f"{kustomization_key.label} {resource_key.compact_label}",
                tofile=f"{kustomization_key.label} {resource_key.compact_label}",
                n=n,
            )
            size = 0
            for line in diff_text:
                size += len(line)
                if limit_bytes and size > limit_bytes:
                    yield _TRUNCATE
                    break
                yield line


async def perform_external_diff(
    cmd: list[str],
    a: ObjectOutput,
    b: ObjectOutput,
    limit_bytes: int,
) -> AsyncGenerator[str, None]:
    """Generate diffs between the two output objects."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for kustomization_key in _unique_keys(a.content, b.content):
            _LOGGER.debug(
                "Diffing results for Kustomization %s",
                kustomization_key,
            )
            a_resources = a.content.get(kustomization_key, {})
            b_resources = b.content.get(kustomization_key, {})
            keys = _unique_keys(a_resources, b_resources)

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
                result = out.decode("utf-8")
                if limit_bytes and len(result) > limit_bytes:
                    result = result[:limit_bytes] + "\n" + _TRUNCATE
                yield result


def omit_none(obj: Any) -> dict[str, Any]:
    """Creates a dictionary with None values missing."""
    return {k: v for k, v in obj if v is not None}


def perform_yaml_diff(
    a: ObjectOutput,
    b: ObjectOutput,
    n: int,
    limit_bytes: int,
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""

    diffs = []
    for kustomization_key in _unique_keys(a.content, b.content):
        _LOGGER.debug("Diffing results for %s (n=%d)", kustomization_key, n)
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        resource_diffs = []
        for resource_key in _unique_keys(a_resources, b_resources):
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
            if limit_bytes and len(diff_content) > limit_bytes:
                diff_content = diff_content[:limit_bytes] + "\n" + _TRUNCATE
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


def get_helm_release_diff_keys(a: ObjectOutput, b: ObjectOutput) -> list[ResourceKey]:
    """Return HelmRelease resource keys with diffs, by cluster."""
    results: list[ResourceKey] = []
    for kustomization_key in _unique_keys(a.content, b.content):
        _LOGGER.debug("Diffing results for Kustomization %s", kustomization_key)
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in _unique_keys(a_resources, b_resources):
            if resource_key.kind != "HelmRelease":
                continue
            if a_resources.get(resource_key) != b_resources.get(resource_key):
                results.append(resource_key)
    return results


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
    args.add_argument(
        "--strip-attrs",
        help="Labels or annotations to strip from the diff",
        type=_CSV,
    )
    args.add_argument(
        "--limit-bytes",
        help="Maximum bytes for each diff output (0=unlimited)",
        type=int,
        default=0,
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
        yield git_repo.PathSelector(path_orig, sources=kwargs.get("sources"))
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
        args.add_argument(
            "--output-file",
            type=str,
            default="/dev/stdout",
            help="Output file for the results of the command",
        )
        selector.add_ks_selector_flags(args)
        add_diff_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        output: str,
        unified: int,
        strip_attrs: list[str] | None,
        limit_bytes: int,
        output_file: str,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_ks_selector(**kwargs)
        query.helm_release.enabled = False

        content = ObjectOutput(strip_attrs)
        query.kustomization.visitor = content.visitor()
        await git_repo.build_manifest(
            selector=query, options=selector.options(**kwargs)
        )

        orig_content = ObjectOutput(strip_attrs)
        with create_diff_path(query.path, **kwargs) as path_selector:
            query.path = path_selector
            query.kustomization.visitor = orig_content.visitor()
            await git_repo.build_manifest(
                selector=query, options=selector.options(**kwargs)
            )

        if not orig_content.content and not content.content:
            print(selector.not_found("Kustomization", query.kustomization))
            return

        _LOGGER.debug("Diffing content")
        with open(output_file, "w") as file:
            if output == "yaml":
                result = perform_yaml_diff(orig_content, content, unified, limit_bytes)
                for line in result:
                    print(line, file=file)
            elif external_diff := os.environ.get("DIFF"):
                async for line in perform_external_diff(
                    shlex.split(external_diff), orig_content, content, limit_bytes
                ):
                    print(line, file=file)
            else:
                result = perform_object_diff(
                    orig_content, content, unified, limit_bytes
                )
                for line in result:
                    print(line, file=file)


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
            "--output-file",
            type=str,
            default="/dev/stdout",
            help="Output file for the results of the command",
        )
        selector.add_hr_selector_flags(args)
        selector.add_helm_options_flags(args)
        add_diff_flags(args)
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        output: str,
        unified: int,
        strip_attrs: list[str] | None,
        limit_bytes: int,
        output_file: str,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_hr_selector(**kwargs)
        content = ObjectOutput(strip_attrs)
        helm_visitor = HelmVisitor()
        query.kustomization.visitor = content.visitor()
        query.helm_repo.visitor = helm_visitor.repo_visitor()
        query.helm_release.visitor = helm_visitor.release_visitor()
        options = selector.build_helm_options(**kwargs)
        await git_repo.build_manifest(
            selector=query, options=selector.options(**kwargs)
        )

        orig_content = ObjectOutput(strip_attrs)
        orig_helm_visitor = HelmVisitor()
        with create_diff_path(query.path, **kwargs) as path_selector:
            query.path = path_selector
            query.kustomization.visitor = orig_content.visitor()
            query.helm_repo.visitor = orig_helm_visitor.repo_visitor()
            query.helm_release.visitor = orig_helm_visitor.release_visitor()
            await git_repo.build_manifest(
                selector=query, options=selector.options(**kwargs)
            )

        if not helm_visitor.releases and not orig_helm_visitor.releases:
            with open(output_file, "w") as file:
                print(selector.not_found("HelmRelease", query.helm_release), file=file)
            return

        # Find HelmRelease objects with diffs and prune all other HelmReleases from
        # the helm visitors. We assume that the only way for a HelmRelease output
        # to have a diff is if the HelmRelease in the kustomization has a diff.
        # This avoid building unnecessary resources and churn from things like
        # random secret generation.
        diff_resource_keys = get_helm_release_diff_keys(orig_content, content)
        diff_names = {
            resource_key.namespaced_name for resource_key in diff_resource_keys
        }
        helm_visitor.releases = [
            release
            for release in helm_visitor.releases
            if release.namespaced_name in diff_names
        ]
        orig_helm_visitor.releases = [
            release
            for release in orig_helm_visitor.releases
            if release.namespaced_name in diff_names
        ]

        helm_content = ObjectOutput(strip_attrs)
        orig_helm_content = ObjectOutput(strip_attrs)
        with tempfile.TemporaryDirectory() as helm_cache_dir:
            await asyncio.gather(
                helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir), helm_content.visitor(), options
                ),
                orig_helm_visitor.inflate(
                    pathlib.Path(helm_cache_dir), orig_helm_content.visitor(), options
                ),
            )

        with open(output_file, "w") as file:
            if output == "yaml":
                for line in perform_yaml_diff(
                    orig_helm_content, helm_content, unified, limit_bytes
                ):
                    print(line, file=file)
            elif external_diff := os.environ.get("DIFF"):
                async for line in perform_external_diff(
                    shlex.split(external_diff),
                    orig_helm_content,
                    helm_content,
                    limit_bytes,
                ):
                    print(line, file=file)
            else:
                for line in perform_object_diff(
                    orig_helm_content, helm_content, unified, limit_bytes
                ):
                    print(line, file=file)


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
            description="""You may also use flux-local to verify your local changes
                to cluster resources have the desird effect. This is similar to flux
                diff but entirely local. This will run a local kustomize build first
                against the local repo then again against a prior repo revision, then
                prints the output.""",
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
