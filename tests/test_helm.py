"""Tests for helm library."""

from aiofiles.os import mkdir
from pathlib import Path
from typing import Any, Generator
import yaml

import pytest

from flux_local.manifest import HelmRepository
from flux_local.manifest import HelmRelease
from flux_local.kustomize import Kustomize
from flux_local.helm import Helm

TESTDATA_DIR = Path("tests/testdata/") / "helm-repo"

@pytest.fixture(name="tmp_config_path")
def tmp_config_path_fixture(tmp_path_factory: Any) -> Generator[Path, None, None]:
    """Fixture for creating a path used for helm config shared across tests."""
    yield tmp_path_factory.mktemp("test_helm")


@pytest.fixture(name="helm_repos")
async def helm_repos_fixture() -> list[HelmRepository]:
    """Fixture for creating the HelmRepository objects"""
    kustomize = Kustomize.build(TESTDATA_DIR).grep("kind=^HelmRepository$")
    return await kustomize.objects()


@pytest.fixture(name="helm")
async def helm_fixture(tmp_config_path: Path, helm_repos: list[dict[str, any]]) -> Helm:
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
    kustomize = Kustomize.build(TESTDATA_DIR).grep("kind=^HelmRelease$")
    return await kustomize.objects()


async def test_update(helm: Helm) -> None:
    """Test a helm update command."""
    await helm.update()


async def test_template(helm: Helm, helm_releases: list[dict[str, Any]]) -> None:
    """Test helm template command."""
    await helm.update()

    assert len(helm_releases) == 1
    release = helm_releases[0]
    kustomize = await helm.template(
        HelmRelease.from_doc(release),
        release["spec"].get("values")
    )
    docs = await kustomize.grep("kind=ServiceAccount").objects()
    names = [ doc.get("metadata", {}).get("name") for doc in docs ]
    assert names == [
        'metallb-controller',
        'metallb-speaker'
    ]
