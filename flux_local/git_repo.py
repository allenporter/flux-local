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
    FLUXTOMIZE_DOMAIN,
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
    "Options",
]

_LOGGER = logging.getLogger(__name__)

CLUSTER_KUSTOMIZE_KIND = "Kustomization"
KUSTOMIZE_KIND = "Kustomization"
HELM_REPO_KIND = "HelmRepository"
HELM_RELEASE_KIND = "HelmRelease"
CLUSTER_POLICY_KIND = "ClusterPolicy"
GIT_REPO_KIND = "GitRepository"
OCI_REPO_KIND = "OCIRepository"
DEFAULT_NAMESPACE = "flux-system"
DEFAULT_NAME = "flux-system"
GREP_SOURCE_REF_KIND = f"spec.sourceRef.kind={GIT_REPO_KIND}|{OCI_REPO_KIND}"
ERROR_DETAIL_BAD_PATH = "Try specifying another path within the git repo?"
ERROR_DETAIL_BAD_KS = "Is a Kustomization pointing to a path that does not exist?"


@dataclass
class Source:
    """A source is a named mapping from a k8s object name to a path in the git repo.

    This is needed to map the location within a reop if it's not the root. For example,
    you may have a `GitRepository` that is relative to `/` and all if of the
    `Kustomization`s inside may reference paths within it e.g. `/k8s/namespaces/`. But
    you may also have an `OCIRepository` that was built relative to `/k8s/` where the
    `Kustomization`s inside may reference the path relative to that like `/namespaces/`.
    """

    name: str
    """The name of the repository source."""

    root: Path
    """The path name within the repo root."""

    namespace: str
    """The namespace of the repository source."""

    @property
    def source_name(self) -> str:
        """Return the full name of the source."""
        return f"{self.namespace}/{self.name}"

    @classmethod
    def from_str(self, value: str) -> "Source":
        """Parse a Source from key=value string."""
        if "=" not in value:
            raise ValueError("Expected source name=root")
        namespace = "flux-system"
        name, root = value.split("=")
        if "/" in name:
            namespace, name = name.split("/")
        return Source(name=name, root=Path(root), namespace=namespace)


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


FLUXTOMIZE_DOMAIN_FILTER = domain_filter(FLUXTOMIZE_DOMAIN)
KUSTOMIZE_DOMAIN_FILTER = domain_filter(KUSTOMIZE_DOMAIN)


@dataclass
class PathSelector:
    """A pathlib.Path inside of a git repo."""

    path: Path | None = None
    """The path within a repo."""

    sources: list[Source] | None = None
    """A list of repository sources for building relative paths."""

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
    return MetadataSelector(name=DEFAULT_NAME, namespace=DEFAULT_NAMESPACE)


def ks_metadata_selector() -> MetadataSelector:
    """Create a new MetadataSelector for Kustomizations."""
    return MetadataSelector(namespace=DEFAULT_NAMESPACE)


@dataclass
class Options:
    """Options for the resource selector for building manifets."""

    kustomize_flags: list[str] = field(default_factory=list)


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


async def get_fluxtomizations(
    root: Path, relative_path: Path, build: bool
) -> list[Kustomization]:
    """Find all flux Kustomizations in the specified path.

    This may be called repeatedly with different paths to repeatedly collect
    Kustomizations from the repo. Assumes that any flux Kustomization
    for a GitRepository is pointed at this cluster, following normal conventions.
    """
    cmd: kustomize.Kustomize
    if build:
        cmd = (
            kustomize.build(root / relative_path)
            .grep(f"kind={CLUSTER_KUSTOMIZE_KIND}")
            .grep(GREP_SOURCE_REF_KIND)
        )
    else:
        cmd = kustomize.grep(
            f"kind={CLUSTER_KUSTOMIZE_KIND}", root / relative_path
        ).grep(GREP_SOURCE_REF_KIND)
    docs = await cmd.objects()
    return [
        Kustomization.parse_doc(doc) for doc in filter(FLUXTOMIZE_DOMAIN_FILTER, docs)
    ]


# default_path=root_path_selector.relative_path
# sources=path-selector.sources or ()
def adjust_ks_path(
    doc: Kustomization, default_path: Path, sources: list[Source]
) -> Path | None:
    """Make adjustments to the Kustomizations path."""
    # Source path is relative to the search path. Update to have the
    # full prefix relative to the root.
    if not doc.path:
        _LOGGER.debug("Assigning implicit path %s", default_path)
        return default_path

    if doc.source_kind == OCI_REPO_KIND:
        for source in sources:
            if source.name == doc.source_name:
                _LOGGER.debug(
                    "Updated Source for OCIRepository %s: %s", doc.name, doc.path
                )
                return source.root / doc.path

        _LOGGER.info(
            "Unknown cluster source for OCIRepository %s: %s", doc.name, doc.path
        )
        return None

    return Path(doc.path)


async def kustomization_traversal(
    root_path_selector: PathSelector, path_selector: PathSelector, build: bool
) -> list[Kustomization]:
    """Search for kustomizations in the specified path."""

    kustomizations: list[Kustomization] = []
    visited_paths: set[Path] = set()  # Relative paths within the cluster
    visited_ks: set[str] = set()

    path_queue: queue.Queue[Path] = queue.Queue()
    path_queue.put(path_selector.relative_path)
    while not path_queue.empty():
        path = path_queue.get()
        _LOGGER.debug("Visiting path (%s) %s", root_path_selector.path, path)
        try:
            docs = await get_fluxtomizations(root_path_selector.root, path, build=build)
        except FluxException as err:
            detail = ERROR_DETAIL_BAD_KS if visited_paths else ERROR_DETAIL_BAD_PATH
            raise FluxException(
                f"Error building Fluxtomization in '{root_path_selector.root}' "
                f"path '{path}': {err} - {detail}"
            )

        visited_paths |= set({path})

        orig_len = len(docs)
        docs = [doc for doc in docs if doc.namespaced_name not in visited_ks]
        visited_ks |= set({doc.namespaced_name for doc in docs})
        new_len = len(docs)
        _LOGGER.debug("Found %s Kustomizations (%s unique)", orig_len, new_len)

        result_docs = []
        for doc in docs:
            _LOGGER.debug(
                "Kustomization '%s' sourceRef.kind '%s' of '%s'",
                doc.name,
                doc.source_kind,
                doc.source_name,
            )
            if not (
                doc_path := adjust_ks_path(
                    doc, root_path_selector.relative_path, path_selector.sources or []
                )
            ):
                continue
            doc.path = str(doc_path)
            if doc_path not in visited_paths:
                path_queue.put(doc_path)
            else:
                _LOGGER.debug("Already visited %s", doc_path)
            result_docs.append(doc)
        kustomizations.extend(result_docs)
    kustomizations.sort(key=lambda x: (x.namespace, x.name))
    return kustomizations


def node_name(ks: Kustomization) -> str:
    """Return a unique node name for the Kustomization.

    This includes the path since it needs to be unique within the entire
    repository since we support multi-cluster.
    """
    return f"{ks.namespaced_name} @ {ks.id_name}"


async def get_clusters(
    path_selector: PathSelector,
    cluster_selector: MetadataSelector,
    kustomization_selector: MetadataSelector,
) -> list[Cluster]:
    """Load Cluster objects from the specified path."""
    try:
        roots = await get_fluxtomizations(
            path_selector.root, path_selector.relative_path, build=False
        )
    except FluxException as err:
        raise FluxException(
            f"Error building Fluxtomization in '{path_selector.root}' path "
            f"'{path_selector.relative_path}': {err}"
            f"Try specifying another path within the git repo?"
        )
    _LOGGER.debug("roots=%s", roots)
    clusters = [
        Cluster(name=ks.name, namespace=ks.namespace or "", path=ks.path)
        for ks in roots
        if cluster_selector.predicate(ks)
    ]
    build = True
    if not clusters:
        # There are no flux-system Kustomizations within this path. Fall back to
        # assuming everything in the current directory is part of a cluster.
        _LOGGER.debug(
            "No clusters found; Processing as a Kustomization: %s",
            path_selector.relative_path,
        )
        clusters = [
            Cluster(name="cluster", namespace="", path=str(path_selector.relative_path))
        ]
        build = False

    tasks = []
    for cluster in clusters:
        _LOGGER.debug("Building cluster %s %s", cluster.name, cluster.path)
        tasks.append(
            kustomization_traversal(
                path_selector,
                PathSelector(path=Path(cluster.path), sources=path_selector.sources),
                build=build,
            )
        )
    finished = await asyncio.gather(*tasks)
    for cluster, results in zip(clusters, finished):
        cluster.kustomizations = [
            ks for ks in results if kustomization_selector.predicate(ks)
        ]
    clusters.sort(key=lambda x: (x.path, x.namespace, x.name))
    return clusters


async def build_kustomization(
    kustomization: Kustomization,
    cluster_path: Path,
    root: Path,
    kustomization_selector: MetadataSelector,
    helm_release_selector: MetadataSelector,
    helm_repo_selector: MetadataSelector,
    cluster_policy_selector: MetadataSelector,
    kustomize_flags: list[str],
) -> tuple[Iterable[HelmRepository], Iterable[HelmRelease], Iterable[ClusterPolicy]]:
    """Build helm objects for the Kustomization."""
    if (
        not kustomization_selector.enabled
        and not helm_release_selector.enabled
        and not helm_repo_selector.enabled
        and not cluster_policy_selector.enabled
    ):
        return ([], [], [])
    cmd = kustomize.build(root / kustomization.path, kustomize_flags)
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
    options: Options = Options(),
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

    async def update_kustomization(cluster: Cluster) -> None:
        build_tasks = []
        for kustomization in cluster.kustomizations:
            _LOGGER.debug(
                "Processing kustomization '%s': %s",
                kustomization.name,
                kustomization.path,
            )
            build_tasks.append(
                build_kustomization(
                    kustomization,
                    Path(cluster.path),
                    selector.path.root,
                    selector.kustomization,
                    selector.helm_release,
                    selector.helm_repo,
                    selector.cluster_policy,
                    options.kustomize_flags,
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
