"""Tests for helm library."""

from pathlib import Path
from typing import Any, Generator

import pytest
from aiofiles.os import mkdir
from syrupy.assertion import SnapshotAssertion

from flux_local import kustomize
from flux_local.helm import Helm, expand_value_references
from flux_local.manifest import HelmRelease, HelmRepository
from flux_local.git_repo import ResourceSelector, PathSelector, build_manifest

REPO_DIR = Path("tests/testdata/cluster/infrastructure/configs")
RELEASE_DIR = Path("tests/testdata/cluster/infrastructure/controllers")


@pytest.fixture(name="tmp_config_path")
def tmp_config_path_fixture(tmp_path_factory: Any) -> Generator[Path, None, None]:
    """Fixture for creating a path used for helm config shared across tests."""
    yield tmp_path_factory.mktemp("test_helm")


@pytest.fixture(name="helm_repos")
async def helm_repos_fixture() -> list[dict[str, Any]]:
    """Fixture for creating the HelmRepository objects"""
    cmd = kustomize.grep("kind=^HelmRepository$", REPO_DIR)
    return await cmd.objects()


@pytest.fixture(name="helm")
async def helm_fixture(tmp_config_path: Path, helm_repos: list[dict[str, Any]]) -> Helm:
    """Fixture for creating the Helm object."""
    await mkdir(tmp_config_path / "helm")
    await mkdir(tmp_config_path / "cache")
    helm = Helm(
        tmp_config_path / "helm",
        tmp_config_path / "cache",
    )
    helm.add_repos([HelmRepository.parse_doc(repo) for repo in helm_repos])
    return helm


@pytest.fixture(name="helm_releases")
async def helm_releases_fixture() -> list[dict[str, Any]]:
    """Fixture for creating the HelmRelease objects."""
    cmd = kustomize.grep("kind=^HelmRelease$", RELEASE_DIR)
    return await cmd.objects()


async def test_update(helm: Helm) -> None:
    """Test a helm update command."""
    await helm.update()


async def test_template(helm: Helm, helm_releases: list[dict[str, Any]]) -> None:
    """Test helm template command."""
    await helm.update()

    assert len(helm_releases) == 2
    release = helm_releases[0]
    obj = await helm.template(HelmRelease.parse_doc(release))
    docs = await obj.grep("kind=ServiceAccount").objects()
    names = [doc.get("metadata", {}).get("name") for doc in docs]
    assert names == ["metallb-controller", "metallb-speaker"]


async def test_value_references(snapshot: SnapshotAssertion) -> None:
    """Test for expanding value references.""" 
    path = Path("tests/testdata/cluster8")
    selector = ResourceSelector(path=PathSelector(path=path))
    manifest = await build_manifest(selector=selector)
    assert len(manifest.clusters) == 1
    assert len(manifest.clusters[0].kustomizations) == 2
    ks = manifest.clusters[0].kustomizations[0]
    assert ks.name == "apps"
    assert len(ks.helm_releases) == 1
    hr = ks.helm_releases[0]
    assert hr.name == "podinfo"
    assert not hr.values

    updated_hr = expand_value_references(hr, ks)
    assert updated_hr.values == snapshot