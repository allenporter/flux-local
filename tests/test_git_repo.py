"""Tests for git_repo."""

from pathlib import Path
import io
from typing import Any

from flux_local.git_repo import build_manifest, ResourceSelector, ResourceVisitor
from flux_local.manifest import HelmRepository, HelmRelease

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


async def test_build_manifest_ks_path() -> None:
    """Tests for building a kustomization directly."""

    query = ResourceSelector()
    query.path.path = TESTDATA / "apps/prod"
    query.kustomization.namespace = None

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == ""
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
    assert len(cluster.kustomizations) == 3
    assert len(cluster.helm_repos) == 2
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
    assert len(cluster.kustomizations) == 3
    assert len(cluster.helm_repos) == 0
    assert len(cluster.helm_releases) == 2


async def test_kustomization_visitor() -> None:
    """Tests for visiting Kustomizations."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    stream = io.StringIO()

    def write(x: Path, y: Any, z: str | None = None) -> None:
        stream.write(z or "")

    query.kustomization.visitor = ResourceVisitor(content=True, func=write)

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 3
    kustomization = cluster.kustomizations[0]
    assert kustomization.name == "apps"
    assert kustomization.namespace == "flux-system"
    assert kustomization.path == "./tests/testdata/cluster/apps/prod"

    content = stream.getvalue()
    assert content
    assert "kind: HelmRelease" in content
    assert "name: metallb" in content


async def test_helm_repo_visitor() -> None:
    """Tests for visiting a HelmRepository objects."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    objects: list[HelmRepository] = []

    query.helm_repo.visitor = ResourceVisitor(
        content=True, func=lambda x, y, z: objects.append(y)
    )

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 3
    assert len(cluster.helm_repos) == 2
    assert len(cluster.helm_releases) == 2

    assert len(objects) == 2
    obj = objects[0]
    assert obj.name == "bitnami"
    assert obj.namespace == "flux-system"
    obj = objects[1]
    assert obj.name == "podinfo"
    assert obj.namespace == "flux-system"


async def test_helm_release_visitor() -> None:
    """Tests for visiting a HelmRelease objects."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    objects: list[HelmRelease] = []

    query.helm_release.visitor = ResourceVisitor(
        content=True, func=lambda x, y, z: objects.append(y)
    )

    manifest = await build_manifest(selector=query)
    assert len(manifest.clusters) == 1
    cluster = manifest.clusters[0]
    assert cluster.name == "flux-system"
    assert cluster.namespace == "flux-system"
    assert cluster.path == "./tests/testdata/cluster/clusters/prod"
    assert len(cluster.kustomizations) == 3
    assert len(cluster.helm_repos) == 2
    assert len(cluster.helm_releases) == 2

    assert len(objects) == 2
    obj = objects[0]
    assert obj.name == "podinfo"
    assert obj.namespace == "podinfo"
    obj = objects[1]
    assert obj.name == "metallb"
    assert obj.namespace == "metallb"
