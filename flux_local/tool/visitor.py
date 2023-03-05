"""Visitors used by multiple commands."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
import pathlib
import tempfile
import yaml

from flux_local import git_repo
from flux_local.kustomize import Kustomize
from flux_local.helm import Helm
from flux_local.manifest import HelmRelease, Kustomization, HelmRepository


_LOGGER = logging.getLogger(__name__)


# Strip any annotations from kustomize that contribute to diff noise when
# objects are re-ordered in the output
STRIP_ANNOTATIONS = [
    "config.kubernetes.io/index",
    "internal.config.kubernetes.io/index",
]


@dataclass(frozen=True, order=True)
class ResourceKey:
    """Key for a Kustomization object output."""

    cluster_path: str
    kustomization_path: str
    kind: str
    namespace: str
    name: str

    @property
    def label(self) -> str:
        parts = []
        # Either path is a unique identifier within the git repo so prefer the
        # most specific path first.
        if self.kustomization_path:
            parts.append(self.kustomization_path)
            parts.append(" ")
        elif self.cluster_path:
            parts.append(self.cluster_path)
            parts.append(" ")
        parts.append(self.compact_label)
        return "".join(parts)

    @property
    def compact_label(self) -> str:
        parts = []
        parts.append(self.kind)
        parts.append(": ")
        if self.namespace:
            parts.append(self.namespace)
            parts.append("/")
        parts.append(self.name)
        return "".join(parts)


class ResourceOutput(ABC):
    """Helper object for implementing a git_repo.ResourceVisitor that saves content.

    This effectively binds the resource name to the content for later
    inspection by name.
    """

    def visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""
        return git_repo.ResourceVisitor(func=self.call_async)

    @abstractmethod
    async def call_async(
        self,
        cluster_path: pathlib.Path,
        kustomization_path: pathlib.Path,
        doc: Kustomization | HelmRelease | HelmRepository,
        cmd: Kustomize | None,
    ) -> None:
        """Visitor function invoked to record build output."""

    def key_func(
        self,
        cluster_path: pathlib.Path,
        kustomization_path: pathlib.Path,
        resource: Kustomization | HelmRelease | HelmRepository,
    ) -> ResourceKey:
        if isinstance(resource, HelmRepository):
            kind = "HelmRepostiory"
        elif isinstance(resource, HelmRelease):
            kind = "HelmRelease"
        else:
            kind = "Kustomization"
        return ResourceKey(
            cluster_path=str(cluster_path),
            kustomization_path=str(kustomization_path),
            kind=kind,
            namespace=resource.namespace or "",
            name=resource.name or "",
        )


class ContentOutput(ResourceOutput):
    """Resource visitor that build string outputs."""

    def __init__(self) -> None:
        """Initialize KustomizationContentOutput."""
        self.content: dict[ResourceKey, list[str]] = {}

    async def call_async(
        self,
        cluster_path: pathlib.Path,
        kustomization_path: pathlib.Path,
        doc: Kustomization | HelmRelease | HelmRepository,
        cmd: Kustomize | None,
    ) -> None:
        """Visitor function invoked to record build output."""
        if cmd:
            content = await cmd.run()
            lines = content.split("\n")
            if lines[0] != "---":
                lines.insert(0, "---")
            self.content[self.key_func(cluster_path, kustomization_path, doc)] = lines


class ObjectOutput(ResourceOutput):
    """Resource visitor that builds outputs for objects within the kustomization."""

    def __init__(self) -> None:
        """Initialize ObjectOutput."""
        # Map of kustomizations to the map of built objects as lines of the yaml string
        self.content: dict[ResourceKey, dict[ResourceKey, list[str]]] = {}

    async def call_async(
        self,
        cluster_path: pathlib.Path,
        kustomization_path: pathlib.Path,
        doc: Kustomization | HelmRelease | HelmRepository,
        cmd: Kustomize | None,
    ) -> None:
        """Visitor function invoked to build and record resource objects."""
        if cmd:
            contents: dict[ResourceKey, list[str]] = {}
            objects = await cmd.objects()
            for resource in objects:
                if not (kind := resource.get("kind")) or not (
                    metadata := resource.get("metadata")
                ):
                    _LOGGER.warning(
                        "Invalid document did not contain kind or metadata: %s",
                        resource,
                    )
                    continue
                if annotations := metadata.get("annotations"):
                    for key in STRIP_ANNOTATIONS:
                        if key in annotations:
                            del annotations[key]
                    if not annotations:
                        del metadata["annotations"]
                resource_key = ResourceKey(
                    kind=kind,
                    cluster_path=str(cluster_path),
                    kustomization_path=str(kustomization_path),
                    namespace=metadata.get("namespace", doc.namespace),
                    name=metadata.get("name", ""),
                )
                content = yaml.dump(resource, sort_keys=False)
                lines = content.split("\n")
                lines.insert(0, "---")
                contents[resource_key] = lines
            self.content[
                self.key_func(cluster_path, kustomization_path, doc)
            ] = contents


async def inflate_release(
    cluster_path: pathlib.Path,
    helm: Helm,
    release: HelmRelease,
    visitor: git_repo.ResourceVisitor,
    skip_crds: bool,
    skip_secrets: bool,
) -> None:
    cmd = await helm.template(release, skip_crds=skip_crds, skip_secrets=skip_secrets)
    # We can ignore the Kustomiation path since we're essentially grouping by cluster
    await visitor.func(cluster_path, pathlib.Path(""), release, cmd)


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
            cluster_path: pathlib.Path,
            kustomization_path: pathlib.Path,
            doc: Kustomization | HelmRelease | HelmRepository,
            cmd: Kustomize | None,
        ) -> None:
            if not isinstance(doc, HelmRepository):
                raise ValueError(f"Expected HelmRepository: {doc}")
            self.repos[str(cluster_path)] = self.repos.get(str(cluster_path), []) + [
                doc
            ]

        return git_repo.ResourceVisitor(func=add_repo)

    def release_visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""

        async def add_release(
            cluster_path: pathlib.Path,
            kustomization_path: pathlib.Path,
            doc: Kustomization | HelmRelease | HelmRepository,
            cmd: Kustomize | None,
        ) -> None:
            if not isinstance(doc, HelmRelease):
                raise ValueError(f"Expected HelmRelease: {doc}")
            self.releases[str(cluster_path)] = self.releases.get(
                str(cluster_path), []
            ) + [doc]

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
        if not self.releases:
            return
        with tempfile.TemporaryDirectory() as tmp_dir:
            helm = Helm(pathlib.Path(tmp_dir), helm_cache_dir)
            if active_repos := self.active_repos(str(cluster_path)):
                helm.add_repos(active_repos)
                await helm.update()
            tasks = [
                inflate_release(
                    cluster_path, helm, release, visitor, skip_crds, skip_secrets
                )
                for release in self.releases.get(str(cluster_path), [])
            ]
            _LOGGER.debug("Waiting for tasks to inflate %s", cluster_path)
            await asyncio.gather(*tasks)
