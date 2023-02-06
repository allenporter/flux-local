"""Tests for helm library."""

from pathlib import Path
from typing import Any, Generator

import pytest
from aiofiles.os import mkdir

from flux_local import kustomize
from flux_local.helm import Helm
from flux_local.manifest import HelmRelease, HelmRepository

TESTDATA_DIR = Path("tests/testdata/") / "helm-repo"


@pytest.fixture(name="tmp_config_path")
def tmp_config_path_fixture(tmp_path_factory: Any) -> Generator[Path, None, None]:
    """Fixture for creating a path used for helm config shared across tests."""
    yield tmp_path_factory.mktemp("test_helm")


@pytest.fixture(name="helm_repos")
async def helm_repos_fixture() -> list[dict[str, Any]]:
    """Fixture for creating the HelmRepository objects"""
    cmd = kustomize.grep("kind=^HelmRepository$", TESTDATA_DIR)
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
    for repo in helm_repos:
        helm.add_repo(HelmRepository.from_doc(repo))
    return helm


@pytest.fixture(name="helm_releases")
async def helm_releases_fixture() -> list[dict[str, Any]]:
    """Fixture for creating the HelmRelease objects."""
    cmd = kustomize.grep("kind=^HelmRelease$", TESTDATA_DIR)
    return await cmd.objects()


async def test_update(helm: Helm) -> None:
    """Test a helm update command."""
    await helm.update()


async def test_template(helm: Helm, helm_releases: list[dict[str, Any]]) -> None:
    """Test helm template command."""
    await helm.update()

    assert len(helm_releases) == 1
    release = helm_releases[0]
    obj = await helm.template(
        HelmRelease.from_doc(release), release["spec"].get("values")
    )
    docs = await obj.grep("kind=ServiceAccount").objects()
    names = [doc.get("metadata", {}).get("name") for doc in docs]
    assert names == ["metallb-controller", "metallb-speaker"]
