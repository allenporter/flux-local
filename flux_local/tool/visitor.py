"""Visitors used by multiple commands."""

import asyncio
from dataclasses import dataclass
import logging
import pathlib
import tempfile

from flux_local import git_repo
from flux_local.kustomize import Kustomize
from flux_local.helm import Helm
from flux_local.manifest import HelmRelease, Kustomization, HelmRepository


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class ResourceKey:
    """Key for a Kustomization object output."""

    path: str
    namespace: str | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        return f"{self.path} - {self.namespace}/{self.name}"


class ResourceContentOutput:
    """Helper object for implementing a git_repo.ResourceVisitor that saves content.

    This effectively binds the resource name to the content for later
    inspection by name.
    """

    def __init__(self) -> None:
        """Initialize KustomizationContentOutput."""
        self.content: dict[ResourceKey, list[str]] = {}

    def visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""
        return git_repo.ResourceVisitor(func=self.call_async)

    async def call_async(
        self,
        path: pathlib.Path,
        doc: Kustomization | HelmRelease | HelmRepository,
        cmd: Kustomize | None,
    ) -> None:
        """Visitor function invoked to record build output."""
        if cmd:
            content = await cmd.run()
            lines = content.split("\n")
            if lines[0] != "---":
                lines.insert(0, "---")
            self.content[self.key_func(path, doc)] = lines

    def key_func(
        self,
        path: pathlib.Path,
        resource: Kustomization | HelmRelease | HelmRepository,
    ) -> ResourceKey:
        return ResourceKey(
            path=str(path), namespace=resource.namespace, name=resource.name
        )


async def inflate_release(
    cluster_path: pathlib.Path,
    helm: Helm,
    release: HelmRelease,
    visitor: git_repo.ResourceVisitor,
    skip_crds: bool,
    skip_secrets: bool,
) -> None:
    cmd = await helm.template(release, skip_crds=skip_crds, skip_secrets=skip_secrets)
    await visitor.func(cluster_path, release, cmd)


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

        async def add_repo(
            path: pathlib.Path,
            doc: Kustomization | HelmRelease | HelmRepository,
            cmd: Kustomize | None,
        ) -> None:
            if not isinstance(doc, HelmRepository):
                raise ValueError(f"Expected HelmRepository: {doc}")
            self.repos[str(path)] = self.repos.get(str(path), []) + [doc]

        return git_repo.ResourceVisitor(func=add_repo)

    def release_visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""

        async def add_release(
            path: pathlib.Path,
            doc: Kustomization | HelmRelease | HelmRepository,
            cmd: Kustomize | None,
        ) -> None:
            if not isinstance(doc, HelmRelease):
                raise ValueError(f"Expected HelmRelease: {doc}")
            self.releases[str(path)] = self.releases.get(str(path), []) + [doc]

        return git_repo.ResourceVisitor(func=add_release)

    async def inflate(
        self,
        helm_cache_dir: pathlib.Path,
        visitor: git_repo.ResourceVisitor,
        skip_crds: bool,
        skip_secrets: bool,
    ) -> None:
        """Expand and notify about HelmReleases discovered."""
        cluster_paths = set(list(self.releases)) | set(list(self.repos))
        tasks = [
            self.inflate_cluster(
                helm_cache_dir,
                pathlib.Path(cluster_path),
                visitor,
                skip_crds,
                skip_secrets,
            )
            for cluster_path in cluster_paths
        ]
        _LOGGER.debug("Waiting for cluster inflation to complete")
        await asyncio.gather(*tasks)

    async def inflate_cluster(
        self,
        helm_cache_dir: pathlib.Path,
        cluster_path: pathlib.Path,
        visitor: git_repo.ResourceVisitor,
        skip_crds: bool,
        skip_secrets: bool,
    ) -> None:
        _LOGGER.debug("Inflating Helm charts in cluster %s", cluster_path)
        with tempfile.TemporaryDirectory() as tmp_dir:
            helm = Helm(pathlib.Path(tmp_dir), helm_cache_dir)
            helm.add_repos(self.active_repos(str(cluster_path)))
            await helm.update()
            tasks = [
                inflate_release(
                    cluster_path, helm, release, visitor, skip_crds, skip_secrets
                )
                for release in self.releases.get(str(cluster_path), [])
            ]
            _LOGGER.debug("Waiting for tasks to inflate %s", cluster_path)
            await asyncio.gather(*tasks)
