"""Flux-local diff action."""

import asyncio
import functools
import os
from argparse import ArgumentParser, _SubParsersAction as SubParsersAction
from contextlib import contextmanager
import logging
import pathlib
import shlex
import tempfile
from typing import cast, Generator, Any


from flux_local import git_repo
from flux_local.visitor import HelmVisitor, ObjectOutput
from flux_local.resource_diff import (
    get_helm_release_diff_keys,
    perform_yaml_diff,
    perform_external_diff,
    perform_object_diff,
    build_helm_dependency_map,
    perform_json_diff,
)

from . import selector

_LOGGER = logging.getLogger(__name__)

# Type for command line flags of comma separated list
_CSV = functools.partial(str.split, sep=",")


def add_diff_flags(args: ArgumentParser) -> None:
    """Add shared diff flags."""
    args.add_argument(
        "--output",
        "-o",
        choices=["diff", "yaml", "object", "json"],
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
        "--branch-orig",
        help="Branch to compare against using worktree",
        type=str,
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

    # Compute relative to the orgiinal worktree not the worktree below
    relative_path = selector.relative_path
    with git_repo.create_worktree(
        selector.repo, existing_branch=kwargs.get("branch_orig")
    ) as worktree:
        yield git_repo.PathSelector(pathlib.Path(worktree) / relative_path)


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
            elif output == "json":
                result = perform_json_diff(orig_content, content, unified, limit_bytes)
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
        query.oci_repo.visitor = helm_visitor.repo_visitor()
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
            query.oci_repo.visitor = orig_helm_visitor.repo_visitor()
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
        # to have a diff is if any of the resources that the HelmRelease
        # depends on in the kustomization has a diff. This avoids needing to
        # template every possible helm chart when nothing as changed.
        dependency_map = build_helm_dependency_map(orig_helm_visitor, helm_visitor)
        diff_resource_keys = get_helm_release_diff_keys(
            orig_content, content, dependency_map
        )
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
            elif output == "json":
                for line in perform_json_diff(
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
