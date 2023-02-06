"""Tests for manifest library."""

import json
from pathlib import Path

import pytest
import yaml

from flux_local.manifest import (
    Cluster,
    HelmRelease,
    HelmRepository,
    Manifest,
    ManifestException,
    read_manifest,
    write_manifest,
)

TESTDATA_DIR = Path("tests/testdata/helm-repo")


def test_parse_helm_release() -> None:
    """Test parsing a helm release doc."""

    release = HelmRelease.from_doc(
        yaml.load(
            (TESTDATA_DIR / "metallb-release.yaml").read_text(), Loader=yaml.CLoader
        )
    )
    assert release.name == "metallb"
    assert release.namespace == "metallb"
    assert release.chart.name == "metallb"
    assert release.chart.version == "4.1.14"
    assert release.chart.repo_name == "bitnami"
    assert release.chart.repo_namespace == "flux-system"


def test_parse_helm_repository() -> None:
    """Test parsing a helm repository doc."""

    repo = HelmRepository.from_doc(
        yaml.load((TESTDATA_DIR / "sources.yaml").read_text(), Loader=yaml.CLoader)
    )
    assert repo.name == "bitnami"
    assert repo.namespace == "flux-system"
    assert repo.url == "https://charts.bitnami.com/bitnami"


async def test_read_manifest_invalid_file() -> None:
    """Test reading an invalid manifest file."""
    with pytest.raises(ManifestException, match="Manifest file malformed"):
        await read_manifest(Path("/dev/null"))


async def test_write_manifest_file() -> None:
    """Test reading an invalid manifest file."""
    await write_manifest(Path("/dev/null"), Manifest(clusters=[]))


async def test_read_write_empty_manifest(tmp_path: Path) -> None:
    """Test serializing and reading back a manifest."""
    manifest = Manifest(clusters=[])
    await write_manifest(tmp_path / "file.yaml", manifest)
    new_manifest = await read_manifest(tmp_path / "file.yaml")
    assert not new_manifest.clusters


async def test_read_write_manifest(tmp_path: Path) -> None:
    """Test serializing and reading back a manifest."""
    manifest = Manifest(
        clusters=[Cluster(name="cluster", path="./example", kustomizations=[])]
    )
    await write_manifest(tmp_path / "file.yaml", manifest)
    new_manifest = await read_manifest(tmp_path / "file.yaml")
    assert json.loads(new_manifest.json()) == {
        "clusters": [
            {
                "name": "cluster",
                "path": "./example",
                "kustomizations": [],
            },
        ]
    }
