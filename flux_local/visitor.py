"""Module for holding state while visiting resources.

This is used internally primarily for keeping state when building output
or computing diffs.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
import pathlib
import tempfile
import yaml

from flux_local import git_repo, image
from flux_local.helm import Helm, Options
from flux_local.kustomize import Kustomize
from flux_local.manifest import (
    HelmRelease,
    Kustomization,
    HelmRepository,
    Manifest,
    OCIRepository,
    NamedResource,
    strip_resource_attributes,
    STRIP_ATTRIBUTES,
)


_LOGGER = logging.getLogger(__name__)


ResourceType = Kustomization | HelmRelease | HelmRepository | OCIRepository


@dataclass(frozen=True, order=True)
class ResourceKey:
    """Key for a Kustomization object output."""

    kustomization_path: str
    kind: str
    namespace: str | None
    name: str

    @property
    def label(self) -> str:
        parts = []
        # Either path is a unique identifier within the git repo so prefer the
        # most specific path first.
        if self.kustomization_path and self.kustomization_path != ".":
            parts.append(self.kustomization_path)
            parts.append(" ")
        parts.append(self.compact_label)
        return "".join(parts)

    @property
    def namespaced_name(self) -> str:
        if self.namespace:
            return f"{self.namespace}/{self.name}"
        return self.name

    @property
    def compact_label(self) -> str:
        return f"{self.kind}: {self.namespaced_name}"

    @property
    def named_resource(self) -> NamedResource:
        return NamedResource(
            kind=self.kind,
            name=self.name,
            namespace=self.namespace,
        )


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
        kustomization_path: pathlib.Path,
        doc: ResourceType,
        cmd: Kustomize | None,
    ) -> None:
        """Visitor function invoked to record build output."""

    def key_func(
        self,
        kustomization_path: pathlib.Path,
        resource: ResourceType,
    ) -> ResourceKey:
        return ResourceKey(
            kustomization_path=str(kustomization_path),
            kind=resource.__class__.__name__,
            namespace=resource.namespace,
            name=resource.name,
        )


class ContentOutput(ResourceOutput):
    """Resource visitor that build string outputs."""

    def __init__(self) -> None:
        """Initialize KustomizationContentOutput."""
        self.content: dict[ResourceKey, list[str]] = {}

    async def call_async(
        self,
        kustomization_path: pathlib.Path,
        doc: ResourceType,
        cmd: Kustomize | None,
    ) -> None:
        """Visitor function invoked to record build output."""
        if cmd:
            content = await cmd.run()
            lines = content.split("\n")
            if lines[0] != "---":
                lines.insert(0, "---")
            self.content[self.key_func(kustomization_path, doc)] = lines


class ImageOutput(ResourceOutput):
    """Resource visitor that builds outputs for objects within the kustomization."""

    def __init__(self) -> None:
        """Initialize ObjectOutput."""
        self.image_visitor = image.ImageVisitor()
        self.repo_visitor = self.image_visitor.repo_visitor()

    async def call_async(
        self,
        kustomization_path: pathlib.Path,
        doc: ResourceType,
        cmd: Kustomize | None,
    ) -> None:
        """Visitor function invoked to build and record resource objects."""
        if cmd and isinstance(doc, HelmRelease):
            objects = await cmd.objects()
            for obj in objects:
                if obj.get("kind") in self.repo_visitor.kinds:
                    self.repo_visitor.func(doc.namespaced_name, obj)

    def update_manifest(self, manifest: Manifest) -> None:
        """Update the manifest with the images found in the repo."""
        for cluster in manifest.clusters:
            for kustomization in cluster.kustomizations:
                for helm_release in kustomization.helm_releases:
                    if images := self.image_visitor.images.get(
                        helm_release.namespaced_name
                    ):
                        helm_release.images = list(images)
                        helm_release.images.sort()


class ObjectOutput(ResourceOutput):
    """Resource visitor that builds outputs for objects within the kustomization."""

    def __init__(self, strip_attributes: list[str] | None) -> None:
        """Initialize ObjectOutput."""
        # Map of kustomizations to the map of built objects as lines of the yaml string
        self.content: dict[ResourceKey, dict[ResourceKey, list[str]]] = {}
        self.strip_attributes = [
            *STRIP_ATTRIBUTES,
            *(strip_attributes or []),
        ]

    async def call_async(
        self,
        kustomization_path: pathlib.Path,
        doc: ResourceType,
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
                # Remove common noisy labels
                strip_resource_attributes(resource, self.strip_attributes)

                resource_key = ResourceKey(
                    kind=kind,
                    kustomization_path=str(kustomization_path),
                    namespace=metadata.get("namespace", doc.namespace),
                    name=metadata.get("name", ""),
                )
                content = yaml.dump(resource, sort_keys=False)
                lines = content.split("\n")
                lines.insert(0, "---")
                contents[resource_key] = lines
            self.content[self.key_func(kustomization_path, doc)] = contents


async def inflate_release(
    helm: Helm,
    release: HelmRelease,
    visitor: git_repo.ResourceVisitor,
    options: Options,
) -> None:
    cmd = await helm.template(release, options)
    # We can ignore the Kustomiation path since we're essentially grouping by cluster
    await visitor.func(pathlib.Path(""), release, cmd)


class HelmVisitor:
    """Helper that visits Helm related objects and handles inflation."""

    def __init__(self) -> None:
        """Initialize KustomizationContentOutput."""
        self.repos: list[HelmRepository | OCIRepository] = []
        self.releases: list[HelmRelease] = []

    @property
    def active_repos(self) -> list[HelmRepository | OCIRepository]:
        """Return HelpRepositories referenced by a HelmRelease."""
        repo_keys: set[str] = {
            release.chart.repo_full_name for release in self.releases
        }
        return [repo for repo in self.repos if repo.repo_name in repo_keys]

    def repo_visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""

        async def add_repo(
            kustomization_path: pathlib.Path,
            doc: ResourceType,
            cmd: Kustomize | None,
        ) -> None:
            if not isinstance(doc, HelmRepository) and not isinstance(
                doc, OCIRepository
            ):
                raise ValueError(f"Expected HelmRepository or OCIRepository: {doc}")
            self.repos.append(doc)

        return git_repo.ResourceVisitor(func=add_repo)

    def release_visitor(self) -> git_repo.ResourceVisitor:
        """Return a git_repo.ResourceVisitor that points to this object."""

        async def add_release(
            kustomization_path: pathlib.Path,
            doc: ResourceType,
            cmd: Kustomize | None,
        ) -> None:
            if not isinstance(doc, HelmRelease):
                raise ValueError(f"Expected HelmRelease: {doc}")
            self.releases.append(doc)

        return git_repo.ResourceVisitor(func=add_release)

    async def inflate(
        self,
        helm_cache_dir: pathlib.Path,
        visitor: git_repo.ResourceVisitor,
        options: Options,
    ) -> None:
        """Expand and notify about HelmReleases discovered."""
        _LOGGER.debug("Inflating Helm charts in cluster")
        if not self.releases:
            return
        with tempfile.TemporaryDirectory() as tmp_dir:
            helm = Helm(pathlib.Path(tmp_dir), helm_cache_dir)
            if active_repos := self.active_repos:
                helm.add_repos(active_repos)
                await helm.update()
            tasks = []
            for release in self.releases:
                if options.skip_invalid_paths and await helm.is_invalid_local_path(release):
                    _LOGGER.info(
                        "Skipping HelmRelease %s with invalid path %s",
                        release.name,
                        release.chart.repo_full_name,
                    )
                    continue
                tasks.append(
                    inflate_release(
                        helm,
                        release,
                        visitor,
                        options,
                    )
                )
            _LOGGER.debug("Waiting for inflate tasks to complete")
            await asyncio.gather(*tasks)
