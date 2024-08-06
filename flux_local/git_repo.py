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
from collections import deque
from collections.abc import Callable, Awaitable
from functools import cache
from pathlib import Path
from typing import Any, Generator

import git

from . import kustomize, values
from .exceptions import FluxException, KustomizePathException
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
    ConfigMap,
    Secret,
    SECRET_KIND,
    CONFIG_MAP_KIND,
    OCIRepository,
)
from .exceptions import InputException
from .context import trace_context

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

    root: Path | None
    """The path name within the repo root."""

    namespace: str | None
    """The namespace of the repository source."""

    @property
    def source_name(self) -> str:
        """Return the full name of the source."""
        return f"{self.namespace}/{self.name}"

    @classmethod
    def from_str(self, value: str) -> "Source":
        """Parse a Source from key=value string."""
        root: Path | None = None
        if "=" in value:
            name, root_str = value.split("=")
            root = Path(root_str)
        else:
            name = value
        namespace: str | None = None
        if "/" in name:
            namespace, name = name.split("/")
        return Source(name=name, root=root, namespace=namespace)


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
            Kustomization
            | HelmRelease
            | HelmRepository
            | ClusterPolicy
            | OCIRepository,
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
class DocumentVisitor:
    """Invoked when a document is visited so the caller can intercept.

    This is similar to a resource visitor, but it visits the unparsed documents
    since they may not have explicit schemas in this project.
    """

    kinds: list[str]
    """The resource kinds of documents to visit."""

    func: Callable[[str, dict[str, Any]], None]
    """Function called with the resource and optional content.

    The function arguments are:
      - parent: The namespaced name of the Fluxtomization or HelmRelease
      - doc: The resource object (e.g. Pod, ConfigMap, HelmRelease, etc)
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
        [Kustomization | HelmRelease | HelmRepository | ClusterPolicy | OCIRepository],
        bool,
    ]:
        """A predicate that selects Kustomization objects."""

        def predicate(
            obj: (
                Kustomization
                | HelmRelease
                | HelmRepository
                | ClusterPolicy
                | OCIRepository
            ),
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

    oci_repo: MetadataSelector = field(default_factory=MetadataSelector)
    """OCIRepository objects to return."""

    cluster_policy: MetadataSelector = field(default_factory=MetadataSelector)
    """ClusterPolicy objects to return."""

    doc_visitor: DocumentVisitor | None = None
    """Raw objects to visit."""


def is_allowed_source(sources: list[Source]) -> Callable[[Kustomization], bool]:
    """Return true if this Kustomization is from an allowed source."""

    def _filter(doc: Kustomization) -> bool:
        if not sources:
            return True
        for source in sources:
            if source.name == doc.source_name and (
                source.namespace is None or source.namespace == doc.source_namespace
            ):
                return True
        return False

    return _filter


def adjust_ks_path(doc: Kustomization, selector: PathSelector) -> Path | None:
    """Make adjustments to the Kustomizations path."""
    # Source path is relative to the search path. Update to have the
    # full prefix relative to the root.
    if not doc.path:
        _LOGGER.debug("Assigning implicit path %s", selector.relative_path)
        return selector.relative_path

    if doc.source_kind == OCI_REPO_KIND:
        for source in selector.sources or []:
            if source.name == doc.source_name:
                _LOGGER.debug(
                    "Updated Source for OCIRepository %s: %s", doc.name, doc.path
                )
                if not source.root:
                    _LOGGER.info("OCIRepository source has no root specified")
                    continue
                return source.root / doc.path

        _LOGGER.info(
            "Unknown cluster source for OCIRepository %s: %s", doc.name, doc.path
        )
        return None

    path = Path(doc.path)
    if path.is_absolute():
        return path.relative_to("/")
    return path


class CachableBuilder:
    """Wrappwr around flux_build that caches contents."""

    def __init__(self) -> None:
        """Initialize CachableBuilder."""
        self._cache: dict[str, kustomize.Kustomize] = {}

    async def build(
        self, kustomization: Kustomization, path: Path
    ) -> kustomize.Kustomize:
        key = f"{kustomization.namespaced_name} @ {path}"
        if cmd := self._cache.get(key):
            return cmd
        cmd = kustomize.flux_build(kustomization, path)
        cmd = await cmd.stash()
        self._cache[key] = cmd
        return cmd

    def remove(self, kustomization: Kustomization) -> None:
        """Remove the kustomization value from the cache."""
        target_key = f"{kustomization.namespaced_name} @"
        for key in list(self._cache.keys()):
            if key.startswith(target_key):
                _LOGGER.debug("Invalidated cache %s", key)
                del self._cache[key]


@dataclass
class VisitResult:
    """Result of visiting a kustomization."""

    kustomizations: list[Kustomization]
    config_maps: list[ConfigMap]
    secrets: list[Secret]

    def __post_init__(self) -> None:
        """Validate the object"""
        unique = {ks.namespaced_name for ks in self.kustomizations}
        if len(unique) != len(self.kustomizations):
            ks_names = [ks.namespaced_name for ks in self.kustomizations]
            dupes = list(filter(lambda x: ks_names.count(x) > 1, ks_names))
            raise FluxException(
                f"Detected multiple Fluxtomizations with the same name: {dupes}. "
                "This indicates either (1) an incorrect Kustomization which needs to be fixed "
                "or (2) a multi-cluster setup which requires flux-local to run with a more strict --path."
            )


async def visit_kustomization(
    selector: PathSelector,
    builder: CachableBuilder,
    path: Path,
    visit_ks: Kustomization | None,
) -> VisitResult:
    """Visit a path and return a list of Kustomizations."""

    _LOGGER.debug("Visiting path (%s) %s", selector.path, path)
    label = visit_ks.namespaced_name if visit_ks else str(path)

    kinds = [CLUSTER_KUSTOMIZE_KIND, CONFIG_MAP_KIND, SECRET_KIND]

    with trace_context(f"Kustomization '{label}'"):
        cmd: kustomize.Kustomize
        if visit_ks is None:
            cmd = kustomize.filter_resources(kinds, selector.root / path)
        else:
            cmd = await builder.build(visit_ks, selector.root / path)
            cmd = cmd.filter_resources(kinds)
        cmd = await cmd.stash()
        ks_cmd = cmd.grep(GREP_SOURCE_REF_KIND)
        cfg_cmd = cmd.filter_resources([CONFIG_MAP_KIND, SECRET_KIND])

        try:
            ks_docs = await ks_cmd.objects()
            cfg_docs = await cfg_cmd.objects()
        except KustomizePathException as err:
            raise FluxException(err) from err
        except FluxException as err:
            if visit_ks is None:
                raise FluxException(
                    f"Error building Fluxtomization in '{selector.root}' "
                    f"path '{path}': {ERROR_DETAIL_BAD_PATH} {err}"
                ) from err
            raise FluxException(
                f"Error building Fluxtomization '{visit_ks.namespaced_name}' "
                f"path '{path}': {ERROR_DETAIL_BAD_KS} {err}"
            ) from err

    return VisitResult(
        kustomizations=list(
            filter(
                is_allowed_source(selector.sources or []),
                [
                    Kustomization.parse_doc(doc)
                    for doc in filter(FLUXTOMIZE_DOMAIN_FILTER, ks_docs)
                ],
            )
        ),
        config_maps=[
            ConfigMap.parse_doc(doc)
            for doc in cfg_docs
            if doc.get("kind") == CONFIG_MAP_KIND
        ],
        secrets=[
            Secret.parse_doc(doc) for doc in cfg_docs if doc.get("kind") == SECRET_KIND
        ],
    )


async def kustomization_traversal(
    selector: PathSelector, builder: CachableBuilder
) -> list[Kustomization]:
    """Search for kustomizations in the specified path."""

    response_kustomizations: list[Kustomization] = []
    visited_paths: set[Path] = set()  # Relative paths within the cluster
    visited_ks: set[str] = set()

    path_queue: deque[tuple[Path, Kustomization | None]] = deque()
    path_queue.append((selector.relative_path, None))
    cluster_config = values.cluster_config([], [])
    while path_queue:
        # Fully empty the queue, running all tasks in parallel
        tasks = []
        while path_queue:
            (path, visit_ks) = path_queue.popleft()

            if path in visited_paths:
                _LOGGER.debug("Already visited %s", path)
                continue
            visited_paths.add(path)

            tasks.append(visit_kustomization(selector, builder, path, visit_ks))

        # Find new kustomizations
        kustomizations = []
        for result in await asyncio.gather(*tasks):
            cluster_config = values.merge_cluster_config(
                cluster_config, result.secrets, result.config_maps
            )
            for ks in result.kustomizations:
                if ks.namespaced_name in visited_ks:
                    continue
                kustomizations.append(ks)
                visited_ks.add(ks.namespaced_name)
        _LOGGER.debug("Found %s new Kustomizations", len(kustomizations))

        # Queue up new paths to visit to find more kustomizations
        for ks in kustomizations:
            _LOGGER.debug(
                "Kustomization '%s' sourceRef.kind '%s' of '%s'",
                ks.name,
                ks.source_kind,
                ks.source_name,
            )
            if not (ks_path := adjust_ks_path(ks, selector)):
                continue
            ks.path = str(ks_path)
            if ks.postbuild_substitute_from:
                values.expand_postbuild_substitute_reference(
                    ks,
                    cluster_config,
                )

            path_queue.append((ks_path, ks))
            response_kustomizations.append(ks)

    response_kustomizations.sort(key=lambda x: (x.namespace, x.name))
    return response_kustomizations


def node_name(ks: Kustomization) -> str:
    """Return a unique node name for the Kustomization.

    This includes the path since it needs to be unique within the entire
    repository since we support multi-cluster.
    """
    return f"{ks.namespaced_name} @ {ks.id_name}"


async def build_kustomization(
    kustomization: Kustomization,
    cluster_path: Path,
    selector: ResourceSelector,
    kustomize_flags: list[str],
    builder: CachableBuilder,
) -> None:
    """Build helm objects for the Kustomization and update state."""

    root: Path = selector.path.root
    kustomization_selector: MetadataSelector = selector.kustomization
    helm_repo_selector: MetadataSelector = selector.helm_repo
    oci_repo_selector: MetadataSelector = selector.oci_repo
    helm_release_selector: MetadataSelector = selector.helm_release
    cluster_policy_selector: MetadataSelector = selector.cluster_policy
    if (
        not kustomization_selector.enabled
        and not helm_repo_selector.enabled
        and not oci_repo_selector.visitor
        and not helm_release_selector.enabled
        and not cluster_policy_selector.enabled
        and not selector.doc_visitor
    ):
        return

    with trace_context(f"Build '{kustomization.namespaced_name}'"):
        cmd = await builder.build(kustomization, root / kustomization.path)
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
            await kustomization_selector.visitor.func(
                Path(kustomization.path),
                kustomization,
                cmd,
            )

        kinds = []
        # Needed for expanding postbuild substitutions and value references
        kinds.append(CONFIG_MAP_KIND)
        if helm_repo_selector.enabled:
            kinds.append(HELM_REPO_KIND)
        if oci_repo_selector.enabled:
            kinds.append(OCI_REPO_KIND)
        if helm_release_selector.enabled:
            kinds.append(HELM_RELEASE_KIND)
            # Needed for expanding value references
            kinds.append(SECRET_KIND)
        if cluster_policy_selector.enabled:
            kinds.append(CLUSTER_POLICY_KIND)
        if selector.doc_visitor:
            kinds.extend(selector.doc_visitor.kinds)
        if not kinds:
            return

        docs = await cmd.filter_resources(kinds).objects(
            target_namespace=kustomization.target_namespace
        )

        if selector.doc_visitor:
            doc_kinds = set(selector.doc_visitor.kinds)
            for doc in docs:
                if doc.get("kind") not in doc_kinds:
                    continue
                selector.doc_visitor.func(kustomization.namespaced_name, doc)

        kustomization.helm_repos = list(
            filter(
                helm_repo_selector.predicate,
                [
                    HelmRepository.parse_doc(doc)
                    for doc in docs
                    if doc.get("kind") == HELM_REPO_KIND
                ],
            )
        )
        kustomization.oci_repos = list(
            filter(
                oci_repo_selector.predicate,
                [
                    OCIRepository.parse_doc(doc)
                    for doc in docs
                    if doc.get("kind") == OCI_REPO_KIND
                ],
            )
        )
        kustomization.helm_releases = list(
            filter(
                helm_release_selector.predicate,
                [
                    HelmRelease.parse_doc(doc)
                    for doc in docs
                    if doc.get("kind") == HELM_RELEASE_KIND
                ],
            )
        )
        kustomization.cluster_policies = list(
            filter(
                cluster_policy_selector.predicate,
                [
                    ClusterPolicy.parse_doc(doc)
                    for doc in docs
                    if doc.get("kind") == CLUSTER_POLICY_KIND
                ],
            )
        )
        kustomization.config_maps = [
            ConfigMap.parse_doc(doc)
            for doc in docs
            if doc.get("kind") == CONFIG_MAP_KIND
        ]
        kustomization.secrets = [
            Secret.parse_doc(doc) for doc in docs if doc.get("kind") == SECRET_KIND
        ]


def _ready_kustomizations(
    kustomizations: list[Kustomization], visited: set[str]
) -> tuple[list[Kustomization], list[Kustomization]]:
    """Split the kustomizations into those that are ready vs pending."""
    ready = []
    pending = []
    for kustomization in kustomizations:
        if not_ready := (set(kustomization.depends_on or {}) - visited):
            _LOGGER.debug(
                "Kustomization %s waiting for %s",
                kustomization.namespaced_name,
                not_ready,
            )
            pending.append(kustomization)
        else:
            ready.append(kustomization)
    return (ready, pending)


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

    builder = CachableBuilder()

    with trace_context(f"Cluster '{str(selector.path.path)}'"):
        results = await kustomization_traversal(selector.path, builder)
        clusters = [
            Cluster(
                path=str(selector.path.relative_path),
                kustomizations=[
                    ks for ks in results if selector.kustomization.predicate(ks)
                ],
            )
        ]

        async def update_kustomization(cluster: Cluster) -> None:
            queue = [*cluster.kustomizations]
            visited: set[str] = set()
            while queue:
                build_tasks = []
                (ready, pending) = _ready_kustomizations(queue, visited)
                for kustomization in ready:
                    _LOGGER.debug(
                        "Processing kustomization '%s': %s",
                        kustomization.name,
                        kustomization.path,
                    )

                    if kustomization.postbuild_substitute_from:
                        values.expand_postbuild_substitute_reference(
                            kustomization,
                            values.ks_cluster_config(cluster.kustomizations),
                        )
                        # Clear the cache to remove any previous builds that are
                        # missing the postbuild substitutions.
                        builder.remove(kustomization)

                    build_tasks.append(
                        build_kustomization(
                            kustomization,
                            Path(cluster.path),
                            selector,
                            options.kustomize_flags,
                            builder,
                        )
                    )
                if not build_tasks:
                    raise FluxException(
                        "Internal error: Unexpected loop without build tasks"
                    )
                await asyncio.gather(*build_tasks)
                visited.update([ks.namespaced_name for ks in ready])
                queue = pending

        # Validate all Kustomizations have valid dependsOn attributes since later
        # we'll be using them to order processing.
        for cluster in clusters:
            all_ks = set([ks.namespaced_name for ks in cluster.kustomizations])
            for ks in cluster.kustomizations:
                ks.validate_depends_on(all_ks)

        kustomization_tasks = []
        # Expand and visit Kustomizations
        for cluster in clusters:
            kustomization_tasks.append(update_kustomization(cluster))
        await asyncio.gather(*kustomization_tasks)

        # Handle any HelmRelease value references
        for cluster in clusters:
            for kustomization in cluster.kustomizations:
                kustomization.helm_releases = [
                    values.expand_value_references(helm_release, kustomization)
                    for helm_release in kustomization.helm_releases
                ]

        # Visit Helm resources
        for cluster in clusters:
            if selector.helm_repo.visitor:
                for kustomization in cluster.kustomizations:
                    for helm_repo in kustomization.helm_repos:
                        await selector.helm_repo.visitor.func(
                            Path(kustomization.path),
                            helm_repo,
                            None,
                        )

            if selector.oci_repo.visitor:
                for kustomization in cluster.kustomizations:
                    for oci_repo in kustomization.oci_repos:
                        await selector.oci_repo.visitor.func(
                            Path(kustomization.path),
                            oci_repo,
                            None,
                        )

            if selector.helm_release.visitor:
                for kustomization in cluster.kustomizations:
                    for helm_release in kustomization.helm_releases:
                        await selector.helm_release.visitor.func(
                            Path(kustomization.path),
                            helm_release,
                            None,
                        )

            if selector.cluster_policy.visitor:
                for kustomization in cluster.kustomizations:
                    for cluster_policy in kustomization.cluster_policies:
                        await selector.cluster_policy.visitor.func(
                            Path(kustomization.path),
                            cluster_policy,
                            None,
                        )

        return Manifest(clusters=clusters)


@contextlib.contextmanager
def create_worktree(
    repo: git.repo.Repo, existing_branch: str | None = None
) -> Generator[Path, None, None]:
    """Create a ContextManager for a new git worktree in the current repo.

    This is used to get a fork of the current repo without any local changes
    in order to produce a diff.
    Specifying existing_branch allows  to compare the current state with the existing branch.
    """
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp_dir:
        _LOGGER.debug("Creating worktree in %s", tmp_dir)
        if existing_branch is None:
            # Add --detach to avoid creating a branch since we will not make modifications
            repo.git.worktree("add", "--detach", str(tmp_dir))
        else:
            repo.git.worktree("add", str(tmp_dir), existing_branch)
        os.chdir(tmp_dir)
        yield Path(tmp_dir)
    _LOGGER.debug("Restoring to %s", orig)
    # The temp directory should now be removed and this prunes the worktree
    repo.git.worktree("prune")
    os.chdir(orig)
