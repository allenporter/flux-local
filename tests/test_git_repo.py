"""Tests for git_repo."""

from pathlib import Path
import io
from typing import Any
import pytest
from unittest.mock import patch

from flux_local.git_repo import (
    build_manifest,
    ResourceSelector,
    ResourceVisitor,
    kustomization_traversal,
    Source,
    PathSelector,
    make_clusters,
)
from flux_local.kustomize import Kustomize
from flux_local.manifest import Kustomization

TESTDATA = Path("tests/testdata/cluster")


async def test_build_manifest() -> None:
    """Tests for building the manifest."""

    manifest = await build_manifest(TESTDATA)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 4
    assert len(cluster.helm_repos) == 3
    assert len(cluster.helm_releases) == 3


async def test_build_manifest_ks_path() -> None:
    """Tests for building a kustomization directly."""

    query = ResourceSelector()
    query.path.path = TESTDATA / "apps/prod"
    query.kustomization.namespace = None

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "cluster"
    assert cluster.namespace == ""
    assert cluster.path == "tests/testdata/cluster/apps/prod"
    assert len(cluster.kustomizations) == 1
    assert len(cluster.helm_repos) == 0
    assert len(cluster.helm_releases) == 1


async def test_cluster_selector_disabled() -> None:
    """Tests for building the manifest."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.cluster.enabled = False

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 0


async def test_kustomization_selector_disabled() -> None:
    """Tests for building the manifest."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.kustomization.enabled = False

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 0


async def test_helm_release_selector_disabled() -> None:
    """Tests for building the manifest with helm releases disabled."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.helm_release.enabled = False

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 4
    assert len(cluster.helm_repos) == 3
    assert len(cluster.helm_releases) == 0


async def test_helm_repo_selector_disabled() -> None:
    """Tests for building the manifest with helm repos disabled."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.helm_repo.enabled = False

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 4
    assert len(cluster.helm_repos) == 0
    assert len(cluster.helm_releases) == 3


async def test_kustomization_visitor() -> None:
    """Tests for visiting Kustomizations."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    stream = io.StringIO()
    visits: list[tuple[str, str, str, str]] = []

    async def write(w: Path, x: Path, y: Any, cmd: Kustomize | None) -> None:
        visits.append((str(w), str(x), y.namespace, y.name))
        if cmd:
            stream.write(await cmd.run())

    query.kustomization.visitor = ResourceVisitor(func=write)

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 4
    kustomization = cluster.kustomizations[0]
    assert kustomization.name == "apps"
    assert kustomization.namespace == "flux-system"
    assert kustomization.path == "./tests/testdata/cluster/apps/prod"

    visits.sort()
    assert visits == [
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/apps/prod",
            "flux-system",
            "apps",
        ),
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/clusters/prod",
            "flux-system",
            "flux-system",
        ),
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/infrastructure/configs",
            "flux-system",
            "infra-configs",
        ),
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/infrastructure/controllers",
            "flux-system",
            "infra-controllers",
        ),
    ]

    content = stream.getvalue()
    assert content
    assert "kind: HelmRelease" in content
    assert "name: metallb" in content


async def test_helm_repo_visitor() -> None:
    """Tests for visiting a HelmRepository objects."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    visits: list[tuple[str, str, str, str]] = []

    async def append(w: Path, x: Path, y: Any, z: Any) -> None:
        visits.append((str(w), str(x), y.namespace, y.name))

    query.helm_repo.visitor = ResourceVisitor(
        func=append,
    )

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 4
    assert len(cluster.helm_repos) == 3
    assert len(cluster.helm_releases) == 3

    visits.sort()
    assert visits == [
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/infrastructure/configs",
            "flux-system",
            "bitnami",
        ),
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/infrastructure/configs",
            "flux-system",
            "podinfo",
        ),
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/infrastructure/configs",
            "flux-system",
            "weave-charts",
        ),
    ]


async def test_helm_release_visitor() -> None:
    """Tests for visiting a HelmRelease objects."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    visits: list[tuple[str, str, str, str]] = []

    async def append(w: Path, x: Path, y: Any, z: Any) -> None:
        visits.append((str(w), str(x), y.namespace, y.name))

    query.helm_release.visitor = ResourceVisitor(
        func=append,
    )

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 4
    assert len(cluster.helm_repos) == 3
    assert len(cluster.helm_releases) == 3

    visits.sort()
    assert visits == [
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/apps/prod",
            "podinfo",
            "podinfo",
        ),
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/infrastructure/controllers",
            "flux-system",
            "weave-gitops",
        ),
        (
            "tests/testdata/cluster/clusters/prod",
            "tests/testdata/cluster/infrastructure/controllers",
            "metallb",
            "metallb",
        ),
    ]


@pytest.mark.parametrize(
    "path",
    [
        "kubernetes/flux/",
        "./kubernetes/flux",
        "kubernetes/flux",
    ],
)
async def test_kustomization_traversal(path: str) -> None:
    """Tests for finding Kustomizations."""

    results = [
        # First traversal
        [
            Kustomization(
                name="cluster-apps",
                namespace="flux-system",
                path="./kubernetes/apps",
                source_path="apps.yaml",
            ),
            Kustomization(
                name="cluster",
                namespace="flux-system",
                path="./kubernetes/flux",
                source_path="config/cluster.yaml",
            ),
        ],
        # Second traversal
        [
            Kustomization(
                name="cluster-apps-rook-ceph",
                namespace="flux-system",
                path="./kubernetes/apps/rook-ceph/rook-ceph/app",
                source_path="rook-ceph/rook-ceph/ks.yaml",
            ),
            Kustomization(
                name="cluster-apps-volsync",
                namespace="flux-system",
                path="./kubernetes/apps/volsync/volsync/app",
                source_path="volsync/volsync/ks.yaml",
            ),
        ],
        # The returned kustomizations point to subdirectories that have
        # already been searched so no need to search further.
    ]
    paths = []

    async def fetch(root: Path, p: Path) -> list[Kustomization]:
        nonlocal paths, results
        paths.append((str(root), str(p)))
        return results.pop(0)

    with patch("flux_local.git_repo.PathSelector.root", Path("/home/example")), patch(
        "flux_local.git_repo.get_flux_kustomizations", fetch
    ):
        kustomizations = await kustomization_traversal(
            path_selector=PathSelector(path=Path(path))
        )
    assert len(kustomizations) == 4
    assert paths == [
        ("/home/example", "kubernetes/flux"),
        ("/home/example", "kubernetes/apps"),
    ]

    clusters = make_clusters(kustomizations)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.name == "cluster"
    assert cluster.namespace == "flux-system"
    assert [ks.path for ks in cluster.kustomizations] == [
        "./kubernetes/flux",
        "./kubernetes/apps",
        "./kubernetes/apps/rook-ceph/rook-ceph/app",
        "./kubernetes/apps/volsync/volsync/app",
    ]


async def test_kustomization_traversal_multi_cluster() -> None:
    """Test discovery of multiple clusters in the repo."""

    results = [
        # First traversal
        [
            Kustomization(
                name="cluster",
                namespace="flux-system",
                path="./clusters/prod",
                source_path="clusters/prod/flux-system/gotk-sync.yaml",
            ),
            Kustomization(
                name="cluster",
                namespace="flux-system",
                path="./clusters/dev",
                source_path="clusters/dev/flux-system/gotk-sync.yaml",
            ),
            Kustomization(
                name="certmanager",
                namespace="flux-system",
                path="./certmanager/dev",
                source_path="clusters/dev/certmanager.yaml",
            ),
            # crds are referenced by both clusters
            Kustomization(
                name="crds",
                namespace="flux-system",
                path="./crds",
                source_path="clusters/dev/crds.yaml",
            ),
            Kustomization(
                name="certmanager",
                namespace="flux-system",
                path="./certmanager/prod",
                source_path="clusters/prod/certmanager.yaml",
            ),
            Kustomization(
                name="crds",
                namespace="flux-system",
                path="./crds",
                source_path="clusters/prod/crds.yaml",
            ),
        ],
    ]
    paths = []

    async def fetch(root: Path, p: Path) -> list[Kustomization]:
        nonlocal paths, results
        paths.append((str(root), str(p)))
        return results.pop(0)

    with patch("flux_local.git_repo.PathSelector.root", Path("/home/example")), patch(
        "flux_local.git_repo.get_flux_kustomizations", fetch
    ):
        kustomizations = await kustomization_traversal(
            path_selector=PathSelector(path=Path("."))
        )
    assert len(kustomizations) == 6
    # We don't need to visit the clusters subdirectories because the original
    # traversal was at the root
    assert paths == [
        ("/home/example", "."),
    ]

    clusters = make_clusters(kustomizations)
    assert len(clusters) == 2
    cluster = clusters[0]
    assert cluster.name == "cluster"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./clusters/dev"
    assert [ks.path for ks in cluster.kustomizations] == [
        "./certmanager/dev",
        "./clusters/dev",
        "./crds",
    ]
    cluster = clusters[1]
    assert cluster.name == "cluster"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./clusters/prod"
    assert [ks.path for ks in cluster.kustomizations] == [
        "./certmanager/prod",
        "./clusters/prod",
        "./crds",
    ]


def test_source() -> None:
    """Test parsing a source from a string."""
    source = Source.from_str("cluster=./k8s")
    assert source.name == "cluster"
    assert source.namespace == "flux-system"
    assert str(source.root) == "k8s"


def test_source_with_namespace() -> None:
    """Test parsing a source from a string."""
    source = Source.from_str("flux-system2/cluster=./k8s")
    assert source.name == "cluster"
    assert source.namespace == "flux-system2"
    assert str(source.root) == "k8s"
