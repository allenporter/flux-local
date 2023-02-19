"""Library for operating on a local repo and building Manifests.

This will build a `manifest.Manifest` from a cluster repo. This follows the
pattern of building kustomizations, then reading helm releases (though it will
not evaluate the templates). The resulting `Manifest` contains all the necessary
information to do basic checks on objects in the cluster (e.g. run templates
from unit tests).

Example usage:

```python
from flux_local import git_repo


selector = git_repo.Selector(
    helm_release=git_repo.MetadataSelector(
        namespace="podinfo"
    )
)
manifest = await git_repo.build_manifest(selector)
for cluster in manifest:
    print(f"Found cluster: {cluster.path}")
    for kustomization in cluster.kustomizations:
        print(f"Found kustomization: {kustomization.path}")
        for release in kustomization.helm_releases:
            print(f"Found helm release: {release.release_name}")
```

"""

import contextlib
from dataclasses import dataclass, field
import logging
import os
import tempfile
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, Generator

import git

from . import kustomize
from .manifest import (
    CLUSTER_KUSTOMIZE_DOMAIN,
    KUSTOMIZE_DOMAIN,
    Cluster,
    HelmRelease,
    HelmRepository,
    Kustomization,
    Manifest,
)

__all__ = [
    "build_manifest",
    "ResourceSelector",
    "PathSelector",
    "MetadataSelector",
]

_LOGGER = logging.getLogger(__name__)

CLUSTER_KUSTOMIZE_NAME = "flux-system"
CLUSTER_KUSTOMIZE_KIND = "Kustomization"
KUSTOMIZE_KIND = "Kustomization"
HELM_REPO_KIND = "HelmRepository"
HELM_RELEASE_KIND = "HelmRelease"
DEFAULT_NAMESPACE = "flux-system"


@cache
def git_repo(path: Path | None = None) -> git.repo.Repo:
    """Return the local git repo path."""
    if path is None:
        return git.repo.Repo(os.getcwd(), search_parent_directories=True)
    return git.repo.Repo(str(path), search_parent_directories=True)


@cache
def repo_root(repo: git.repo.Repo | None = None) -> Path:
    """Return the local git repo path."""
    if repo is None:
        repo = git_repo()
    return Path(repo.git.rev_parse("--show-toplevel"))


def domain_filter(version: str) -> Callable[[dict[str, Any]], bool]:
    """Return a yaml doc filter for specified resource version."""

    def func(doc: dict[str, Any]) -> bool:
        if api_version := doc.get("apiVersion"):
            if api_version.startswith(version):
                return True
        return False

    return func


CLUSTER_KUSTOMIZE_DOMAIN_FILTER = domain_filter(CLUSTER_KUSTOMIZE_DOMAIN)
KUSTOMIZE_DOMAIN_FILTER = domain_filter(KUSTOMIZE_DOMAIN)


@dataclass
class PathSelector:
    """A pathlib.Path inside of a git repo."""

    path: Path | None = None
    """The path within a repo."""

    @property
    def repo(self) -> git.repo.Repo:
        """Return the local git repo."""
        return git_repo(self.path)

    @property
    def root(self) -> Path:
        """Return the local git repo root."""
        return repo_root(self.repo)

    @property
    def process_path(self) -> Path:
        """Return the path to process."""
        return self.path or self.root


@dataclass
class MetadataSelector:
    """A filter for objects to select from the cluster."""

    name: str | None = None
    """Resources returned will match this name."""

    namespace: str | None = None
    """Resources returned will be from this namespace."""

    @property
    def predicate(self) -> Callable[[Kustomization | HelmRelease], bool]:
        """A predicate that selects Kustomization objects."""

        def predicate(obj: Kustomization | HelmRelease) -> bool:
            if self.name and obj.name != self.name:
                return False
            if self.namespace and obj.namespace != self.namespace:
                return False
            return True

        return predicate


def ks_metadata_selector() -> MetadataSelector:
    """Create a new MetadataSelector for Kustomizations."""
    return MetadataSelector(namespace=DEFAULT_NAMESPACE)


@dataclass
class ResourceSelector:
    """A filter for objects to select from the cluster.

    This is invoked when iterating over objects in the cluster to decide which
    resources should be inflated and returned, to avoid iterating over
    unnecessary resources.
    """

    path: PathSelector = field(default_factory=PathSelector)
    """Path to find a repo of local flux Kustomization objects"""

    kustomization: MetadataSelector = field(default_factory=ks_metadata_selector)
    """Kustomization names to return, or all if empty."""

    helm_release: MetadataSelector = field(default_factory=MetadataSelector)


async def get_clusters(path: Path) -> list[Cluster]:
    """Load Cluster objects from the specified path."""
    cmd = kustomize.grep(f"kind={CLUSTER_KUSTOMIZE_KIND}", path).grep(
        f"metadata.name={CLUSTER_KUSTOMIZE_NAME}"
    )
    docs = await cmd.objects()
    return [
        Cluster.from_doc(doc) for doc in docs if CLUSTER_KUSTOMIZE_DOMAIN_FILTER(doc)
    ]


async def get_cluster_kustomizations(path: Path) -> list[Kustomization]:
    """Load Kustomization objects from the specified path."""
    cmd = kustomize.grep(f"kind={KUSTOMIZE_KIND}", path).grep(
        f"metadata.name={CLUSTER_KUSTOMIZE_NAME}",
        invert=True,
    )
    docs = await cmd.objects()
    return [
        Kustomization.from_doc(doc)
        for doc in docs
        if CLUSTER_KUSTOMIZE_DOMAIN_FILTER(doc)
    ]


async def get_kustomizations(path: Path) -> list[dict[str, Any]]:
    """Load Kustomization objects from the specified path."""
    cmd = kustomize.grep(f"kind={KUSTOMIZE_KIND}", path)
    docs = await cmd.objects()
    return list(filter(KUSTOMIZE_DOMAIN_FILTER, docs))


async def build_manifest(
    path: Path | None = None, selector: ResourceSelector = ResourceSelector()
) -> Manifest:
    """Build a Manifest object from the local cluster.

    This will locate all Kustomizations that represent clusters, then find all
    the Kustomizations within that cluster, as well as all relevant Helm
    resources.
    """
    if path:
        selector.path = PathSelector(path=path)

    _LOGGER.debug("Processing cluster with selector %s", selector)
    clusters = await get_clusters(selector.path.process_path)
    if len(clusters) > 0:
        for cluster in clusters:
            _LOGGER.debug("Processing cluster: %s", cluster.path)
            cluster.kustomizations = await get_cluster_kustomizations(
                selector.path.root / cluster.path.lstrip("./")
            )
    elif selector.path.path:
        _LOGGER.debug("No clusters found; Processing as a Kustomization: %s", selector)
        # The argument path may be a Kustomization inside a cluster. Create a synthetic
        # cluster with any found Kustomizations
        cluster = Cluster(name="", path="")
        objects = await get_kustomizations(selector.path.path)
        if objects:
            cluster.kustomizations = [
                Kustomization(name="", path=str(selector.path.path))
            ]
            clusters.append(cluster)

    for cluster in clusters:
        cluster.kustomizations = list(
            filter(selector.kustomization.predicate, cluster.kustomizations)
        )
        for kustomization in cluster.kustomizations:
            _LOGGER.debug("Processing kustomization: %s", kustomization.path)
            cmd = kustomize.build(selector.path.root / kustomization.path)
            kustomization.helm_repos = [
                HelmRepository.from_doc(doc)
                for doc in await cmd.grep(f"kind=^{HELM_REPO_KIND}$").objects()
            ]
            kustomization.helm_releases = list(
                filter(
                    selector.helm_release.predicate,
                    [
                        HelmRelease.from_doc(doc)
                        for doc in await cmd.grep(
                            f"kind=^{HELM_RELEASE_KIND}$"
                        ).objects()
                    ],
                )
            )
    return Manifest(clusters=clusters)


@contextlib.contextmanager
def create_worktree(repo: git.repo.Repo) -> Generator[Path, None, None]:
    """Create a ContextManager for a new git worktree in the current repo.

    This is used to get a fork of the current repo without any local changes
    in order to produce a diff.
    """
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp_dir:
        _LOGGER.debug("Creating worktree in %s", tmp_dir)
        # Add --detach to avoid creating a branch since we will not make modifications
        repo.git.worktree("add", "--detach", str(tmp_dir))
        os.chdir(tmp_dir)
        yield Path(tmp_dir)
    _LOGGER.debug("Restoring to %s", orig)
    # The temp directory should now be removed and this prunes the worktree
    repo.git.worktree("prune")
    os.chdir(orig)
