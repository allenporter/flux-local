"""Tests for manifest library."""

from pathlib import Path

import yaml

from flux_local.manifest import HelmRelease, HelmRepository

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
