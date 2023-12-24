"""Tests for helm library."""

import base64
from pathlib import Path
from typing import Any, Generator

import pytest
from aiofiles.os import mkdir
from syrupy.assertion import SnapshotAssertion

from flux_local import kustomize
from flux_local.exceptions import HelmException
from flux_local.helm import Helm, expand_value_references
from flux_local.manifest import (
    HelmRelease,
    HelmRepository,
    ValuesReference,
    Secret,
    ConfigMap,
    Kustomization,
    HelmChart,
)
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
    assert hr.values == snapshot


def test_values_references_with_values_key() -> None:
    """Test for expanding a value reference with a values key."""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values={"test": "test"},
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                namespace="test",
                name="test-values-configmap",
                valuesKey="some-key",
                targetPath="target.path",
            ),
            ValuesReference(
                kind="ConfigMap",
                namespace="test",
                name="test-binary-data-configmap",
                valuesKey="some-key",
            ),
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        config_maps=[
            ConfigMap(
                name="test-values-configmap",
                namespace="test",
                data={"some-key": "example_value"},
            ),
            ConfigMap(
                name="test-binary-data-configmap",
                namespace="test",
                binary_data={
                    "some-key": base64.b64encode(
                        "encoded_key: encoded_value".encode("utf-8")
                    )
                },
            ),
        ],
    )
    updated_hr = expand_value_references(hr, ks)
    assert updated_hr.values == {
        "test": "test",
        "target": {
            "path": "example_value",
        },
        "encoded_key": "encoded_value",
    }


def test_values_references_with_missing_values_key() -> None:
    """Test for expanding a value reference with a values key that is missing."""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values={"test": "test"},
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                namespace="test",
                name="test-values-configmap",
                valuesKey="some-key-does-not-exist",
                targetPath="target.path",
            )
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        config_maps=[
            ConfigMap(
                name="test-values-configmap",
                namespace="test",
                data={"some-key": "example_value"},
            )
        ],
    )
    updated_hr = expand_value_references(hr, ks)
    assert updated_hr.values == {
        "test": "test",
    }


def test_values_references_invalid_yaml() -> None:
    """Test for expanding a value reference with a values key."""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values={"test": "test"},
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                namespace="test",
                name="test-values-configmap",
            )
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        config_maps=[
            ConfigMap(
                name="test-values-configmap",
                namespace="test",
                data={"values.yaml": "not-yaml"},
            )
        ],
    )
    with pytest.raises(HelmException, match=r"valid yaml"):
        expand_value_references(hr, ks)


def test_values_references_invalid_binary_data() -> None:
    """Test for expanding a value reference with an invalid binary data key."""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values={"test": "test"},
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                namespace="test",
                name="test-values-configmap",
            )
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        config_maps=[
            ConfigMap(
                name="test-values-configmap",
                namespace="test",
                binary_data={"values.yaml": "this is not base64 data"},
            )
        ],
    )
    with pytest.raises(HelmException, match=r"Unable to decode binary data"):
        expand_value_references(hr, ks)


def test_values_reference_invalid_target_path() -> None:
    """Test for expanding a value reference with a values key."""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values={
            "test": "test",
            "target": ["a", "b", "c"],
        },
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                namespace="test",
                name="test-values-configmap",
                valuesKey="some-key",
                # Target above is a list
                targetPath="target.path",
            )
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        config_maps=[
            ConfigMap(
                name="test-values-configmap",
                namespace="test",
                data={"some-key": "example_value"},
            )
        ],
    )
    with pytest.raises(HelmException, match=r"values to be a dict"):
        expand_value_references(hr, ks)


def test_values_reference_invalid_configmap_and_secret() -> None:
    """Test a values reference to a config map and secret that do not exist."""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values={"test": "test"},
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                namespace="test",
                name="test-values-configmap",
                optional=False,  # We just log
            ),
            ValuesReference(
                kind="Secret",
                namespace="test",
                name="test-values-secret",
                optional=False,  # We just log
            ),
            ValuesReference(
                kind="UnknownKind",
                namespace="test",
                name="test-values-secret",
            ),
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        config_maps=[],
    )
    updated_hr = expand_value_references(hr, ks)
    # No changes to the values
    assert updated_hr.values == {"test": "test"}


def test_values_references_secret() -> None:
    """Test for expanding a value reference for a secret."""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values={"test": "test"},
        values_from=[
            ValuesReference(
                kind="Secret",
                namespace="test",
                name="test-values-secret",
                valuesKey="some-key1",
                targetPath="target.path1",
            ),
            ValuesReference(
                kind="Secret",
                namespace="test",
                name="test-string-values-secret",
                valuesKey="some-key2",
                targetPath="target.path2",
            ),
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        secrets=[
            Secret.parse_doc(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": "test-values-secret",
                        "namespace": "test",
                    },
                    "data": {
                        "some-key1": base64.b64encode("example-value".encode("utf-8")),
                    },
                }
            ),
            Secret.parse_doc(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": "test-string-values-secret",
                        "namespace": "test",
                    },
                    "stringData": {
                        "some-key2": "example-string-value",
                    },
                }
            ),
        ],
    )
    updated_hr = expand_value_references(hr, ks)
    assert updated_hr.values == {
        "test": "test",
        "target": {
            "path1": "**PLACEHOLDER**",
            "path2": "**PLACEHOLDER**",
        },
    }
