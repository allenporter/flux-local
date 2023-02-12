"""Command line tool for building, diffing, validating flux local repositories."""

import argparse
import asyncio
import difflib
import logging
import pathlib
import tempfile
from typing import AsyncGenerator

import yaml
from aiofiles.os import mkdir
from slugify import slugify

from flux_local import git_repo, kustomize
from flux_local.helm import Helm
from flux_local.manifest import Kustomization

_LOGGER = logging.getLogger(__name__)


CRD_RESOURCE = "CustomResourceDefinition"


async def build_kustomization(
    root: pathlib.Path,
    kustomization: Kustomization,
    helm: Helm | None,
    skip_crds: bool,
) -> AsyncGenerator[str, None]:
    """Flux-local build action for a kustomization."""
    cmds = kustomize.build(root / kustomization.path)
    if skip_crds:
        cmds = cmds.grep(f"kind=^{CRD_RESOURCE}$", invert=True)
    if helm:
        # Exclude HelmReleases and expand below
        cmds = cmds.grep_helm_release(invert=True)
    objs = await cmds.objects()
    yield yaml.dump(objs)

    if not helm:
        return

    for helm_release in kustomization.helm_releases:
        cmds = await helm.template(helm_release, skip_crds=skip_crds)
        objs = await cmds.objects()
        yield yaml.dump(objs)


async def build(
    path: pathlib.Path, enable_helm: bool, skip_crds: bool
) -> AsyncGenerator[str, None]:
    """Flux-local build action."""
    root = git_repo.repo_root(git_repo.git_repo(path))
    manifest = await git_repo.build_manifest(path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        await mkdir(pathlib.Path(tmp_dir) / "cache")

        for cluster in manifest.clusters:
            helm = None
            if enable_helm:
                path = pathlib.Path(tmp_dir) / f"{slugify(cluster.path)}"
                await mkdir(path)

                helm = Helm(path, pathlib.Path(tmp_dir) / "cache")
                helm.add_repos(cluster.helm_repos)
                await helm.update()

            for kustomization in cluster.kustomizations:
                async for content in build_kustomization(
                    root, kustomization, helm, skip_crds
                ):
                    yield content


async def diff(path: pathlib.Path, enable_helm: bool, skip_crds: bool) -> None:
    """Flux-local diff action."""
    repo = git_repo.git_repo(path)

    content1 = []
    with git_repo.create_worktree(repo) as worktree_dir:
        async for content in build(worktree_dir, enable_helm, skip_crds):
            content1.append(content)

    content2 = []
    async for content in build(git_repo.repo_root(repo), enable_helm, skip_crds):
        content2.append(content)

    diff_text = difflib.unified_diff(
        "".join(content1).split("\n"), "".join(content2).split("\n")
    )
    print("\n".join(diff_text))


async def async_main() -> None:
    """An async main implementation."""
    parser = argparse.ArgumentParser(
        description="Manages local kubernetes objects in a flux repository."
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )
    subparsers = parser.add_subparsers(dest="command", help="Command", required=True)

    build_args = subparsers.add_parser(
        "build", help="Build local flux Kustomization target from a local directory"
    )
    build_args.add_argument(
        "path", type=pathlib.Path, help="Path to the kustomization or charts"
    )
    build_args.add_argument(
        "--enable-helm",
        type=bool,
        action=argparse.BooleanOptionalAction,
        help="Enable use of HelmRelease inflation",
    )
    build_args.add_argument(
        "--skip-crds",
        type=bool,
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Allows disabling of outputting CRDs to reduce output size",
    )

    diff_args = subparsers.add_parser(
        "diff", help="Build local flux Kustomization target from a local directory"
    )
    diff_args.add_argument(
        "path", type=pathlib.Path, help="Path to the kustomization or charts"
    )
    diff_args.add_argument(
        "--enable-helm",
        type=bool,
        action=argparse.BooleanOptionalAction,
        help="Enable use of HelmRelease inflation",
    )
    diff_args.add_argument(
        "--skip-crds",
        type=bool,
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Allows disabling of outputting CRDs to reduce output size",
    )

    manifest_args = subparsers.add_parser(
        "manifest", help="Build a yaml manifest file representing the cluster"
    )
    manifest_args.add_argument(
        "path", type=pathlib.Path, help="Path to the kustomization or charts"
    )

    # https://github.com/yaml/pyyaml/issues/89
    yaml.Loader.yaml_implicit_resolvers.pop("=")

    args = parser.parse_args()

    if args.log_level:
        logging.basicConfig(level=args.log_level)

    if args.command == "build":
        async for content in build(args.path, args.enable_helm, args.skip_crds):
            print(content)
        return

    if args.command == "diff":
        await diff(args.path, args.enable_helm, args.skip_crds)
        return

    if args.command == "manifest":
        manifest = await git_repo.build_manifest(args.path)
        print(manifest.yaml())
        return


def main() -> None:
    """Flux-local command line tool main entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
