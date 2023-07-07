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

    results: list[list[Kustomization]] = [
        # First traversal
        [
            Kustomization(
                name="flux-system",
                namespace="flux-system",
                path="./kubernetes/cluster",
                source_path="apps.yaml",
            ),
        ],
        # Second traversal
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
        # Third traversal
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
        [],
        [],
        # The returned kustomizations point to subdirectories that have
        # already been searched so no need to search further.
    ]
    paths = []

    async def fetch(root: Path, p: Path, build: bool) -> list[Kustomization]:
        nonlocal paths, results
        paths.append((str(root), str(p)))
        return results.pop(0)

    with patch("flux_local.git_repo.PathSelector.root", Path("/home/example")), patch(
        "flux_local.git_repo.get_fluxtomizations", fetch
    ):
        kustomizations = await kustomization_traversal(
            root_path_selector=PathSelector(path=Path(path)),
            path_selector=PathSelector(path=Path(path)),
            build=True,
        )
    assert len(kustomizations) == 5
    assert paths == [
        ("/home/example", "kubernetes/flux"),
        ("/home/example", "kubernetes/cluster"),
        ("/home/example", "kubernetes/apps"),
        ("/home/example", "kubernetes/apps/rook-ceph/rook-ceph/app"),
        ("/home/example", "kubernetes/apps/volsync/volsync/app"),
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
