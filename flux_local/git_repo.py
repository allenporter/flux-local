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

import asyncio
import contextlib
from dataclasses import dataclass, field
import logging
import networkx
import os
import tempfile
from collections.abc import Callable, Awaitable, Iterable
from functools import cache
from pathlib import Path
import queue
from typing import Any, Generator

import git

from . import kustomize
from .exceptions import FluxException
from .manifest import (
    CRD_KIND,
    CLUSTER_KUSTOMIZE_DOMAIN,
    KUSTOMIZE_DOMAIN,
    Cluster,
    ClusterPolicy,
    HelmRelease,
    HelmRepository,
    Kustomization,
    Manifest,
    SECRET_KIND,
)
from .exceptions import InputException

__all__ = [
    "build_manifest",
    "ResourceSelector",
    "PathSelector",
    "MetadataSelector",
]

_LOGGER = logging.getLogger(__name__)

CLUSTER_KUSTOMIZE_KIND = "Kustomization"
KUSTOMIZE_KIND = "Kustomization"
HELM_REPO_KIND = "HelmRepository"
HELM_RELEASE_KIND = "HelmRelease"
CLUSTER_POLICY_KIND = "ClusterPolicy"
GIT_REPO_KIND = "GitRepository"
DEFAULT_NAMESPACE = "flux-system"


@cache
def git_repo(path: Path | None = None) -> git.repo.Repo:
    """Return the local git repo path."""
    try:
        if path is None:
            return git.repo.Repo(os.getcwd(), search_parent_directories=True)
        return git.repo.Repo(str(path), search_parent_directories=True)
    except git.GitError as err:
        raise InputException(f"Unable to find input path {path}: {err}") from err


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
    def relative_path(self) -> Path:
        """Return the relative path within the repo.

        This is used when transposing this path on a worktree.
        """
        if not self.process_path.is_absolute():
            return self.process_path
        return self.process_path.resolve().relative_to(self.root.resolve())

    @property
    def process_path(self) -> Path:
        """Return the path to process."""
        return self.path or self.root


@dataclass
class ResourceVisitor:
    """Invoked when a resource is visited to the caller can intercept."""

    func: Callable[
        [
            Path,
            Path,
            Kustomization | HelmRelease | HelmRepository | ClusterPolicy,
            kustomize.Kustomize | None,
        ],
        Awaitable[None],
    ]
    """Function called with the resource and optional content.

    The function arguments are:
      - path: This is the cluster and kustomization paths needed to disambiguate
        when there are multiple clusters in the repository.
      - doc: The resource object (e.g. Kustomization, HelmRelease, etc)
      - cmd: A Kustomize object that can produce the specified object. Only supported
        for Kustomizations.
    """


@dataclass
class MetadataSelector:
    """A filter for objects to select from the cluster."""

    enabled: bool = True
    """If true, this selector may return objects."""

    name: str | None = None
    """Resources returned will match this name."""

    namespace: str | None = None
    """Resources returned will be from this namespace."""

    skip_crds: bool = True
    """If false, CRDs may be processed, depending on the resource type."""

    skip_secrets: bool = True
    """If false, Secrets may be processed, depending on the resource type."""

    visitor: ResourceVisitor | None = None
    """Visitor for the specified object type that can be used for building."""

    @property
    def predicate(
        self,
    ) -> Callable[
        [Kustomization | HelmRelease | Cluster | HelmRepository | ClusterPolicy], bool
    ]:
        """A predicate that selects Kustomization objects."""

        def predicate(
            obj: Kustomization | HelmRelease | Cluster | HelmRepository | ClusterPolicy,
        ) -> bool:
            if not self.enabled:
                return False
            if self.name and obj.name != self.name:
                return False
            if self.namespace and obj.namespace != self.namespace:
                return False
            return True

        return predicate


def cluster_metadata_selector() -> MetadataSelector:
    """Create a new MetadataSelector for Kustomizations."""
    return MetadataSelector(namespace=DEFAULT_NAMESPACE)


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

    cluster: MetadataSelector = field(default_factory=cluster_metadata_selector)
    """Cluster names to return."""

    kustomization: MetadataSelector = field(default_factory=ks_metadata_selector)
    """Kustomization names to return."""

    helm_repo: MetadataSelector = field(default_factory=MetadataSelector)
    """HelmRepository objects to return."""

    helm_release: MetadataSelector = field(default_factory=MetadataSelector)
    """HelmRelease objects to return."""

    cluster_policy: MetadataSelector = field(default_factory=MetadataSelector)
    """ClusterPolicy objects to return."""


async def get_flux_kustomizations(
    root: Path, relative_path: Path
) -> list[Kustomization]:
    """Find all flux Kustomizations in the specified path.

    This may be called repeatedly with different paths to repeatedly collect
    Kustomizations from the repo. Assumes that any flux Kustomization
    for a GitRepository is pointed at this cluster, following normal conventions.
    """
    cmd = kustomize.grep(f"kind={CLUSTER_KUSTOMIZE_KIND}", root / relative_path).grep(
        f"spec.sourceRef.kind={GIT_REPO_KIND}"
    )
    docs = await cmd.objects()
    return [
        Kustomization.parse_doc(doc)
        for doc in filter(CLUSTER_KUSTOMIZE_DOMAIN_FILTER, docs)
    ]


def find_path_parent(search: Path, prefixes: set[Path]) -> Path | None:
    """Return a prefix path that is a parent of the search path."""
    for parent in search.parents:
        if parent in prefixes:
            return parent
    return None


async def kustomization_traversal(path_selector: PathSelector) -> list[Kustomization]:
    """Search for kustomizations in the specified path."""

    kustomizations: list[Kustomization] = []
    visited: set[Path] = set()  # Relative paths within the cluster

    path_queue: queue.Queue[Path] = queue.Queue()
    path_queue.put(path_selector.relative_path)
    root = path_selector.root
    while not path_queue.empty():
        path = path_queue.get()
        _LOGGER.debug("Visiting path (%s) %s", root, path)
        try:
            docs = await get_flux_kustomizations(root, path)
        except FluxException as err:
            if visited:
                raise FluxException(
                    f"Error building Fluxtomization in '{root}' path '{path}': {err}"
                    f"Is a Kustomization pointing to a path that does not exist?"
                )
            raise FluxException(
                f"Error building Fluxtomization in '{root}' path '{path}': {err}"
                f"Try specifying another path within the git repo?"
            )

        # Source path is relative to the search path. Update to have the
        # full prefix relative to the root.
        for kustomization in docs:
            if not kustomization.source_path:
                continue
            _LOGGER.debug(
                "Updating relative path: %s, %s, %s",
                root,
                path,
                kustomization.source_path,
            )
            kustomization.source_path = str(
                ((root / path) / kustomization.source_path).relative_to(root)
            )

        visited |= set({path})

        _LOGGER.debug("Found %s Kustomizations", len(docs))
        for doc in docs:
            found_path = Path(doc.path)
            if not find_path_parent(found_path, visited) and found_path not in visited:
                path_queue.put(found_path)
            else:
                _LOGGER.debug("Already visited %s", found_path)
        kustomizations.extend(docs)
    return kustomizations


def make_clusters(kustomizations: list[Kustomization]) -> list[Cluster]:
    """Convert the flat list of Kustomizations into a Cluster.

    This will reverse engineer which Kustomizations are root nodes for the cluster
    based on the parent paths. Root Kustomizations are made the cluster and everything
    else is made a child.
    """

    # Build a directed graph from a kustomization path to the path
    # of the kustomization that created it.
    graph = networkx.DiGraph()
    parent_paths = set([Path(ks.path) for ks in kustomizations])
    for ks in kustomizations:
        if not ks.source_path:
            raise InputException(
                "Kustomization did not have source path; Old kustomize?"
            )
        path = Path(ks.path)
        source = Path(ks.source_path)
        graph.add_node(path, ks=ks)
        # Find the parent Kustomization that produced this based on the
        # matching the kustomize source parent paths with a Kustomization
        # target path.
        if (
            parent_path := find_path_parent(source, parent_paths)
        ) and parent_path != path:
            _LOGGER.debug("Found parent %s => %s", path, parent_path)
            graph.add_edge(parent_path, path)
        else:
            _LOGGER.debug("No parent for %s (%s)", path, source)

    # Clusters are subgraphs within the graph that are connected, with the root
    # node being the cluster itself. All children Kustomizations are flattended.
    _LOGGER.debug("Creating clusters based on connectivity")
    roots = [node for node, degree in graph.in_degree() if degree == 0]
    roots.sort()
    clusters: list[Cluster] = []
    _LOGGER.debug("roots=%s", roots)
    for root in roots:
        root_ks = graph.nodes[root]["ks"]
        nodes = [root] + list(networkx.descendants(graph, root))
        nodes.sort()
        kustomizations = [graph.nodes[node]["ks"] for node in nodes]
        clusters.append(
            Cluster(
                name=root_ks.name,
                namespace=root_ks.namespace,
                path=root_ks.path,
                kustomizations=kustomizations,
            )
        )
        _LOGGER.debug(
            "Created cluster %s with %s kustomizations", root_ks.name, len(nodes)
        )

    return clusters


async def get_clusters(
    path_selector: PathSelector,
    cluster_selector: MetadataSelector,
    kustomization_selector: MetadataSelector,
) -> list[Cluster]:
    """Load Cluster objects from the specified path."""

    kustomizations = await kustomization_traversal(path_selector)
    clusters = list(filter(cluster_selector.predicate, make_clusters(kustomizations)))
    for cluster in clusters:
        cluster.kustomizations = list(
            filter(kustomization_selector.predicate, cluster.kustomizations)
        )
    return clusters


async def get_kustomizations(path: Path) -> list[dict[str, Any]]:
    """Load Kustomization objects from the specified path."""
    cmd = kustomize.grep(f"kind={KUSTOMIZE_KIND}", path)
    docs = await cmd.objects()
    return list(filter(KUSTOMIZE_DOMAIN_FILTER, docs))


async def build_kustomization(
    kustomization: Kustomization,
    cluster_path: Path,
    root: Path,
    kustomization_selector: MetadataSelector,
    helm_release_selector: MetadataSelector,
    helm_repo_selector: MetadataSelector,
    cluster_policy_selector: MetadataSelector,
) -> tuple[Iterable[HelmRepository], Iterable[HelmRelease], Iterable[ClusterPolicy]]:
    """Build helm objects for the Kustomization."""
    if (
        not kustomization_selector.enabled
        and not helm_release_selector.enabled
        and not helm_repo_selector.enabled
        and not cluster_policy_selector.enabled
    ):
        return ([], [], [])

    cmd = kustomize.build(root / kustomization.path)
    skips = []
    if kustomization_selector.skip_crds:
        skips.append(CRD_KIND)
    if kustomization_selector.skip_secrets:
        skips.append(SECRET_KIND)
    cmd = cmd.skip_resources(skips)
    try:
        cmd = await cmd.stash()
    except FluxException as err:
        raise FluxException(
            f"Error while building Kustomization "
            f"'{kustomization.namespace}/{kustomization.name}' "
            f"(path={kustomization.source_path}): {err}"
        ) from err

    if kustomization_selector.visitor:
        if kustomization_selector.visitor:
            await kustomization_selector.visitor.func(
                cluster_path,
                Path(kustomization.path),
                kustomization,
                cmd,
            )

    if (
        not helm_release_selector.enabled
        and not helm_repo_selector.enabled
        and not cluster_policy_selector.enabled
    ):
        return ([], [], [])

    docs = await cmd.grep(
        f"kind=^({HELM_REPO_KIND}|{HELM_RELEASE_KIND}|{CLUSTER_POLICY_KIND})$"
    ).objects()
    return (
        filter(
            helm_repo_selector.predicate,
            [
                HelmRepository.parse_doc(doc)
                for doc in docs
                if doc.get("kind") == HELM_REPO_KIND
            ],
        ),
        filter(
            helm_release_selector.predicate,
            [
                HelmRelease.parse_doc(doc)
                for doc in docs
                if doc.get("kind") == HELM_RELEASE_KIND
            ],
        ),
        filter(
            cluster_policy_selector.predicate,
            [
                ClusterPolicy.parse_doc(doc)
                for doc in docs
                if doc.get("kind") == CLUSTER_POLICY_KIND
            ],
        ),
    )


async def build_manifest(
    path: Path | None = None,
    selector: ResourceSelector = ResourceSelector(),
) -> Manifest:
    """Build a Manifest object from the local cluster.

    This will locate all Kustomizations that represent clusters, then find all
    the Kustomizations within that cluster, as well as all relevant Helm
    resources.

    The path input parameter is deprecated. Use the PathSelector in `selector` instead.
    """
    if path:
        selector.path = PathSelector(path=path)

    _LOGGER.debug("Processing cluster with selector %s", selector)
    if not selector.cluster.enabled:
        return Manifest(clusters=[])

    clusters = await get_clusters(
        selector.path, selector.cluster, selector.kustomization
    )
    if not clusters and selector.path.path:
        _LOGGER.debug("No clusters found; Processing as a Kustomization: %s", selector)
        # The argument path may be a Kustomization inside a cluster. Create a synthetic
        # cluster with any found Kustomizations
        cluster = Cluster(name="cluster", namespace="", path=str(selector.path.path))
        objects = await get_kustomizations(selector.path.path)
        if objects:
            cluster.kustomizations = [
                Kustomization(name="kustomization", path=str(selector.path.path))
            ]
            clusters.append(cluster)

    async def update_kustomization(cluster: Cluster) -> None:
        build_tasks = []
        for kustomization in cluster.kustomizations:
            _LOGGER.debug("Processing kustomization: %s", kustomization.path)
            build_tasks.append(
                build_kustomization(
                    kustomization,
                    Path(cluster.path),
                    selector.path.root,
                    selector.kustomization,
                    selector.helm_release,
                    selector.helm_repo,
                    selector.cluster_policy,
                )
            )
        results = list(await asyncio.gather(*build_tasks))
        for kustomization, (helm_repos, helm_releases, cluster_policies) in zip(
            cluster.kustomizations,
            results,
        ):
            kustomization.helm_repos = list(helm_repos)
            kustomization.helm_releases = list(helm_releases)
            kustomization.cluster_policies = list(cluster_policies)

    kustomization_tasks = []
    # Expand and visit Kustomizations
    for cluster in clusters:
        kustomization_tasks.append(update_kustomization(cluster))
    await asyncio.gather(*kustomization_tasks)

    # Visit Helm resources
    for cluster in clusters:
        if selector.helm_repo.visitor:
            for kustomization in cluster.kustomizations:
                for helm_repo in kustomization.helm_repos:
                    await selector.helm_repo.visitor.func(
                        Path(cluster.path),
                        Path(kustomization.path),
                        helm_repo,
                        None,
                    )

        if selector.helm_release.visitor:
            for kustomization in cluster.kustomizations:
                for helm_release in kustomization.helm_releases:
                    await selector.helm_release.visitor.func(
                        Path(cluster.path),
                        Path(kustomization.path),
                        helm_release,
                        None,
                    )

        if selector.cluster_policy.visitor:
            for kustomization in cluster.kustomizations:
                for cluster_policy in kustomization.cluster_policies:
                    await selector.cluster_policy.visitor.func(
                        Path(cluster.path),
                        Path(kustomization.path),
                        cluster_policy,
                        None,
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
