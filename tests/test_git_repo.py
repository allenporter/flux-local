"""Tests for git_repo."""

from pathlib import Path

from flux_local.git_repo import build_manifest, ResourceSelector

TESTDATA = Path("tests/testdata/cluster")


async def test_build_manifest() -> None:
    """Tests for building the manifest."""

    manifest = await build_manifest(TESTDATA)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 3
    assert len(cluster.helm_repos) == 2
    assert len(cluster.helm_releases) == 2


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
    """Tests for building the manifest."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.helm_release.enabled = False

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 3
    assert len(cluster.helm_repos) == 0
    assert len(cluster.helm_releases) == 0
