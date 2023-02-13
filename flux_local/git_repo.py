"""Library for operating on a local repo and building Manifests.

This will build a `manifest.Manifest` from a cluster repo. This follows the
pattern of building kustomizations, then reading helm releases (though it will
not evaluate the templates). The resulting `Manifest` contains all the necessary
information to do basic checks on objects in the cluster (e.g. run templates
from unit tests).

Example usage:

```python
from flux_local import repo

manifest = await repo.build_manifest()
for cluster in manifest:
    print(f"Found cluster: {cluster.path}")
    for kustomization in cluster.kustomizations:
        print(f"Found kustomization: {kustomization.path}")
        for release in kustomization.helm_releases:
            print(f"Found helm release: {release.release_name}")
```

"""

import contextlib
from dataclasses import dataclass
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
    "repo_root",
    "build_manifest",
    "ResourceSelector",
]

_LOGGER = logging.getLogger(__name__)

CLUSTER_KUSTOMIZE_NAME = "flux-system"
CLUSTER_KUSTOMIZE_KIND = "Kustomization"
KUSTOMIZE_KIND = "Kustomization"
HELM_REPO_KIND = "HelmRepository"
HELM_RELEASE_KIND = "HelmRelease"


def git_repo(path: Path | None = None) -> git.repo.Repo:
    """Return the local github repo path."""
    if path is None:
        return git.repo.Repo(os.getcwd(), search_parent_directories=True)
    return git.repo.Repo(str(path), search_parent_directories=True)


@cache
def repo_root(repo: git.repo.Repo | None = None) -> Path:
    """Return the local github repo path."""
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
class ResourceSelector:
    """A filter for objects to select from the cluster.

    This is invoked when iterating over objects in the cluster to decide which
    resources should be inflated and returned, to avoid iterating over
    unnecessary resources.
    """

    kustomizations: set[str] | None = None
    """Kustomization names to return, or all if empty."""

    helm_release_namespace: set[str] | None = None
    """HelmReleases returned will be from this namespace, or all if empty."""

    @property
    def kustomization_predicate(self) -> Callable[[Kustomization], bool]:
        """A predicate that selects Kustomization objects."""

        def predicate(ks: Kustomization) -> bool:
            if not self.kustomizations:
                return True
            return ks.name in self.kustomizations

        return predicate

    @property
    def helm_release_predicate(self) -> Callable[[HelmRelease], bool]:
        """A predicate that selects Kustomization objects."""

        def predicate(hr: HelmRelease) -> bool:
            if not self.helm_release_namespace:
                return True
            return hr.namespace in self.helm_release_namespace

        return predicate


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
    root = repo_root(git_repo(path))

    _LOGGER.debug("Processing as a cluster: %s", path or root)
    clusters = await get_clusters(path or root)
    if len(clusters) > 0:
        for cluster in clusters:
            _LOGGER.debug("Processing cluster: %s", cluster.path)
            cluster.kustomizations = await get_cluster_kustomizations(
                root / cluster.path.lstrip("./")
            )
    elif path:
        _LOGGER.debug("No clusters found; Processing as a Kustomization: %s", path)
        # The argument path may be a Kustomization inside a cluster. Create a synthetic
        # cluster with any found Kustomizations
        cluster = Cluster(name="", path="")
        objects = await get_kustomizations(path)
        if objects:
            cluster.kustomizations = [Kustomization(name="", path=str(path))]
            clusters.append(cluster)

    for cluster in clusters:
        cluster.kustomizations = list(
            filter(selector.kustomization_predicate, cluster.kustomizations)
        )
        for kustomization in cluster.kustomizations:
            _LOGGER.debug("Processing kustomization: %s", kustomization.path)
            cmd = kustomize.build(root / kustomization.path)
            kustomization.helm_repos = [
                HelmRepository.from_doc(doc)
                for doc in await cmd.grep(f"kind=^{HELM_REPO_KIND}$").objects()
            ]
            kustomization.helm_releases = list(
                filter(
                    selector.helm_release_predicate,
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
        repo.git.worktree("add", str(tmp_dir))
        os.chdir(tmp_dir)
        yield Path(tmp_dir)
    _LOGGER.debug("Restoring to %s", orig)
    # The temp directory should now be removed and this prunes the worktree
    repo.git.worktree("prune")
    os.chdir(orig)
