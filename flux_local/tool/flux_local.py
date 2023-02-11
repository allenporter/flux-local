"""Command line tool for building, diffing, validating flux local repositories."""

import argparse
import asyncio
import logging
import pathlib
import tempfile

import yaml
from aiofiles.os import mkdir
from slugify import slugify

from flux_local import kustomize, repo
from flux_local.helm import Helm
from flux_local.manifest import Kustomization

_LOGGER = logging.getLogger(__name__)


async def build(
    root: pathlib.Path, kustomization: Kustomization, helm: Helm | None
) -> None:
    """Flux-local command line tool main async entry point."""
    cmds = kustomize.build(root / kustomization.path)
    if helm:
        # Exclude HelmReleases and expand below
        cmds = cmds.grep_helm_release(invert=True)
    objs = await cmds.objects()
    print(yaml.dump(objs))

    if not helm:
        return

    for helm_release in kustomization.helm_releases:
        cmds = await helm.template(helm_release)
        objs = await cmds.objects()
        print(yaml.dump(objs))


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

    #  https://github.com/yaml/pyyaml/issues/89
    yaml.Loader.yaml_implicit_resolvers.pop("=")

    args = parser.parse_args()

    if args.log_level:
        logging.basicConfig(level=args.log_level)

    if args.command == "build":
        root = repo.repo_root(repo.git_repo(args.path))
        manifest = await repo.build_manifest(args.path)

        with tempfile.TemporaryDirectory() as tmp_dir:
            await mkdir(pathlib.Path(tmp_dir) / "cache")

            for cluster in manifest.clusters:
                helm = None
                if args.enable_helm:
                    path = pathlib.Path(tmp_dir) / f"{slugify(cluster.path)}"
                    await mkdir(path)

                    helm = Helm(path, pathlib.Path(tmp_dir) / "cache")
                    helm.add_repos(cluster.helm_repos)
                    await helm.update()

                for kustomization in cluster.kustomizations:
                    await build(root, kustomization, helm)

        return

    if args.command == "manifest":
        manifest = await repo.build_manifest(args.path)
        print(manifest.yaml())
        return


def main() -> None:
    """Flux-local command line tool main entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
