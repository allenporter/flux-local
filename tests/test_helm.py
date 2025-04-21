"""Tests for helm library."""

from pathlib import Path
from typing import Any, Generator

import pytest
from aiofiles.os import mkdir

from flux_local import kustomize
from flux_local.helm import Helm
from flux_local.manifest import (
    HelmRelease,
    HelmRepository,
    OCIRepository,
)


@pytest.fixture(name="helm_repo_dir")
def helm_repo_dir_fixture() -> Path | None:
    return None


@pytest.fixture(name="oci_repo_dir")
def oci_repo_dir_fixture() -> Path | None:
    return None


@pytest.fixture(name="tmp_config_path")
def tmp_config_path_fixture(tmp_path_factory: Any) -> Generator[Path, None, None]:
    """Fixture for creating a path used for helm config shared across tests."""
    yield tmp_path_factory.mktemp("test_helm")


@pytest.fixture(name="helm_repos")
async def helm_repos_fixture(helm_repo_dir: Path | None) -> list[dict[str, Any]]:
    """Fixture for creating the HelmRepository objects"""
    if not helm_repo_dir:
        return []
    cmd = kustomize.grep("kind=^HelmRepository$", helm_repo_dir)
    return await cmd.objects()


@pytest.fixture(name="oci_repos")
async def oci_repos_fixture(oci_repo_dir: Path) -> list[dict[str, Any]]:
    """Fixture for creating the OCIRepositoriy objects"""
    if not oci_repo_dir:
        return []
    cmd = kustomize.grep("kind=^OCIRepository$", oci_repo_dir)
    return await cmd.objects()


@pytest.fixture(name="helm")
async def helm_fixture(
    tmp_config_path: Path,
    helm_repos: list[dict[str, Any]],
    oci_repos: list[dict[str, Any]],
) -> Helm:
    """Fixture for creating the Helm object."""
    await mkdir(tmp_config_path / "helm")
    await mkdir(tmp_config_path / "cache")
    helm = Helm(
        tmp_config_path / "helm",
        tmp_config_path / "cache",
    )
    helm.add_repos([HelmRepository.parse_doc(repo) for repo in helm_repos])
    helm.add_repos([OCIRepository.parse_doc(repo) for repo in oci_repos])
    return helm


@pytest.fixture(name="helm_releases")
async def helm_releases_fixture(release_dir: Path) -> list[dict[str, Any]]:
    """Fixture for creating the HelmRelease objects."""
    cmd = kustomize.grep("kind=^HelmRelease$", release_dir)
    return await cmd.objects()


async def test_update(helm: Helm) -> None:
    """Test a helm update command."""
    await helm.update()


@pytest.mark.parametrize(
    ("helm_repo_dir", "release_dir"),
    [
        (
            Path("tests/testdata/cluster/infrastructure/configs"),
            Path("tests/testdata/cluster/infrastructure/controllers"),
        ),
    ],
)
async def test_template(helm: Helm, helm_releases: list[dict[str, Any]]) -> None:
    """Test helm template command."""
    await helm.update()

    assert len(helm_releases) == 2

    # metallb, no targetNamespace overrides
    release = helm_releases[0]
    obj = await helm.template(HelmRelease.parse_doc(release))
    docs = await obj.grep("kind=ServiceAccount").objects()
    names = [doc.get("metadata", {}).get("name") for doc in docs]
    namespaces = [doc.get("metadata", {}).get("namespace") for doc in docs]
    assert names == ["metallb-controller", "metallb-speaker"]
    assert namespaces == ["metallb", "metallb"]

    # weave-gitops, with targetNamespace overrides
    release = helm_releases[1]
    obj = await helm.template(HelmRelease.parse_doc(release))
    docs = await obj.grep("kind=ServiceAccount").objects()
    names = [doc.get("metadata", {}).get("name") for doc in docs]
    namespaces = [doc.get("metadata", {}).get("namespace") for doc in docs]
    assert names == ["weave-gitops"]
    assert namespaces == ["weave"]


@pytest.mark.parametrize(
    ("oci_repo_dir", "release_dir"),
    [
        (
            Path("tests/testdata/cluster9/apps/podinfo/"),
            Path("tests/testdata/cluster9/apps/podinfo/"),
        ),
    ],
)
async def test_oci_repository(helm: Helm, helm_releases: list[dict[str, Any]]) -> None:
    """Test helm template command."""
    await helm.update()

    assert len(helm_releases) == 1
    release = helm_releases[0]
    obj = await helm.template(HelmRelease.parse_doc(release))
    docs = await obj.grep("kind=Deployment").objects()
    names = [doc.get("metadata", {}).get("name") for doc in docs]
    assert names == ["podinfo"]
