"""Visitors used by multiple commands."""

import asyncio
import logging
import pathlib
import tempfile

import yaml

from flux_local import git_repo
from flux_local.helm import Helm
from flux_local.manifest import HelmRelease, Kustomization, HelmRepository


_LOGGER = logging.getLogger(__name__)


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

    def active_repos(self, cluster_path: str) -> list[HelmRepository]:
        """Return HelpRepositories referenced by a HelmRelease."""
        repo_keys: set[str] = {
            f"{release.chart.repo_namespace}-{release.chart.repo_name}"
            for release in self.releases.get(cluster_path, [])
        }
        return [
            repo
            for repo in self.repos.get(cluster_path, [])
            if repo.repo_name in repo_keys
        ]

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
                helm.add_repos(self.active_repos(str(cluster_path)))
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
