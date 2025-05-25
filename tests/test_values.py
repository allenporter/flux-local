"""Tests for helm library."""

import base64
from pathlib import Path
import yaml
from typing import Any

import pytest
from syrupy.assertion import SnapshotAssertion

from flux_local.exceptions import HelmException
from flux_local.values import (
    expand_value_references,
    expand_postbuild_substitute_reference,
    ks_cluster_config,
    cluster_config,
)
from flux_local.manifest import (
    HelmRelease,
    ValuesReference,
    Secret,
    ConfigMap,
    Kustomization,
    HelmChart,
    SubstituteReference,
)
from flux_local.git_repo import ResourceSelector, PathSelector, build_manifest


async def test_value_references(snapshot: SnapshotAssertion) -> None:
    """Test for expanding value references."""
    path = Path("tests/testdata/cluster8")
    selector = ResourceSelector(path=PathSelector(path=path))
    manifest = await build_manifest(selector=selector)
    assert len(manifest.clusters) == 1
    assert len(manifest.clusters[0].kustomizations) == 2
    ks = manifest.clusters[0].kustomizations[0]
    assert ks.name == "apps"
    assert len(ks.helm_releases) == 2
    hr = ks.helm_releases[0]
    assert hr.name == "podinfo"
    assert hr.values == snapshot
    hr = ks.helm_releases[1]
    assert hr.name == "tailscale-operator"
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
                name="test-values-configmap",
                values_key="some-key",
                target_path="target.path",
            ),
            ValuesReference(
                kind="ConfigMap",
                name="test-binary-data-configmap",
                values_key="some-key",
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


@pytest.mark.parametrize(
    ("inline_values", "expected_values"),
    [
        (
            {
                "redis": {
                    "enabled": True,
                }
            },
            {
                "redis": {
                    "enabled": True,
                }
            },
        ),
        (
            {},
            {
                "redis": {
                    "enabled": False,
                }
            },
        ),
    ],
)
def test_value_reference_ordering(
    inline_values: dict[str, Any], expected_values: dict[str, Any]
) -> None:
    """Test for expanding a value reference with inline values that overwrite."""
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
            **inline_values,
        },
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                name="test-binary-data-configmap",
                values_key="some-key",
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
                name="test-binary-data-configmap",
                namespace="test",
                binary_data={
                    "some-key": base64.b64encode(
                        "redis:\n  enabled: False".encode("utf-8")
                    )
                },
            ),
        ],
    )
    updated_hr = expand_value_references(hr, ks)
    assert updated_hr.values == expected_values


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
                name="test-values-configmap",
                values_key="some-key-does-not-exist",
                target_path="target.path",
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


def test_values_references_with_missing_secret() -> None:
    """Test for expanding a value reference with a missing secret."""
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
                name="test-values-secret",
                values_key="some-key-does-not-exist",
                target_path="target.path",
            )
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
    )
    updated_hr = expand_value_references(hr, ks)
    assert updated_hr.values == {
        "test": "test",
        "target": {
            "path": "..PLACEHOLDER_test-values-secret..",
        },
    }


def test_values_references_with_missing_secret_values_key() -> None:
    """Test for expanding a value reference with a secret key that is missing."""
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
                name="test-values-secret",
                values_key="some-key-does-not-exist",
                target_path="target.path",
            )
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        secrets=[
            Secret(
                name="test-values-secret",
                namespace="test",
                string_data={"some-key": "example_value"},
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
        values={},
        values_from=[
            ValuesReference(
                kind="ConfigMap",
                name="test-values-original-configmap",
            ),
            ValuesReference(
                kind="ConfigMap",
                name="test-values-configmap",
                values_key="some-key",
                # Target above is a list
                target_path="target.path",
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
                name="test-values-original-configmap",
                namespace="test",
                data={
                    "values.yaml": yaml.dump(
                        {
                            "test": "test",
                            "target": ["a", "b", "c"],
                        }
                    ),
                },
            ),
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
                name="test-values-configmap",
                optional=False,  # We just log
            ),
            ValuesReference(
                kind="Secret",
                name="test-values-secret",
                optional=False,  # We just log
            ),
            ValuesReference(
                kind="UnknownKind",
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
                name="test-values-secret",
                values_key="some-key1",
                target_path="target.path1",
            ),
            ValuesReference(
                kind="Secret",
                name="test-string-values-secret",
                values_key="some-key2",
                target_path="target.path2",
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
            "path1": "..PLACEHOLDER_some-key1..",
            "path2": "..PLACEHOLDER_some-key2..",
        },
    }


def test_target_path_character_escapes() -> None:
    """Test for character escapes in the reference target_path"""
    hr = HelmRelease(
        name="test",
        namespace="test",
        chart=HelmChart(
            repo_name="test-repo",
            repo_namespace="flux-system",
            name="test-chart",
            version="test-version",
        ),
        values_from=[
            ValuesReference(
                kind="Secret",
                name="test-character-escape-secret",
                values_key="some-key",
                target_path=r"test.app\.kuber\\netes\.io/na\me\=\[backend]",
            ),
        ],
    )
    ks = Kustomization(
        name="test",
        namespace="test",
        path="example/path",
        helm_releases=[hr],
        secrets=[
            Secret(
                name="test-character-escape-secret",
                namespace="test",
                string_data={"some-key": "example_value"},
            )
        ],
    )
    updated_hr = expand_value_references(hr, ks)
    assert updated_hr.values == {
        "test": {
            r"app.kuber\netes.io/name=[backend]": "example_value",
        },
    }


def test_postbuild_substitute_from() -> None:
    """Test for expanding from a config map."""
    app_ks = Kustomization(
        name="app",
        namespace="test",
        path="example/app",
        postbuild_substitute={"test": "test"},
        postbuild_substitute_from=[
            SubstituteReference(
                kind="ConfigMap",
                name="test-values-configmap",
            ),
            SubstituteReference(
                kind="ConfigMap",
                name="test-binary-data-configmap",
            ),
        ],
    )
    config_maps = [
        ConfigMap(
            name="test-values-configmap",
            namespace="test",
            data={"some_key": "example_value"},
        ),
        ConfigMap(
            name="test-binary-data-configmap",
            namespace="test",
            binary_data={
                "encoded_key": base64.b64encode("encoded_value".encode("utf-8"))
            },
        ),
    ]
    app_ks = expand_postbuild_substitute_reference(
        app_ks, cluster_config([], config_maps)
    )
    assert app_ks.postbuild_substitute == {
        "test": "test",
        "some_key": "example_value",
        "encoded_key": "encoded_value",
    }


def test_postbuild_substitute_from_secret() -> None:
    """Test for expanding from a secret."""
    app_ks = Kustomization(
        name="app",
        namespace="test",
        path="example/app",
        postbuild_substitute={"test": "test"},
        postbuild_substitute_from=[
            SubstituteReference(
                kind="Secret",
                name="test-values-secret",
            ),
        ],
    )
    secrets = [
        Secret(
            name="test-values-secret",
            namespace="test",
            string_data={"some_key": "example_value"},
        ),
    ]
    app_ks = expand_postbuild_substitute_reference(app_ks, cluster_config(secrets, []))
    assert app_ks.postbuild_substitute == {
        "test": "test",
        "some_key": "example_value",
    }


def test_postbuild_substitute_from_invalid_references() -> None:
    """Test for invalid SubstituteReferences."""
    app_ks = Kustomization(
        name="app",
        namespace="test",
        path="example/app",
        postbuild_substitute={"test": "test"},
        postbuild_substitute_from=[
            SubstituteReference(
                kind="Secret",
                name="test-values-secret",
            ),
            SubstituteReference(
                kind="ConfigMap",
                name="test-values-config-map",
            ),
            SubstituteReference(
                kind="Invalid",
                name="test-values-invalid",
            ),
        ],
    )
    app_ks = expand_postbuild_substitute_reference(app_ks, cluster_config([], []))
    assert app_ks.postbuild_substitute == {
        "test": "test",
    }


def test_cluster_config() -> None:
    """Test for ClusterConfig objects."""

    ks1 = Kustomization(
        name="app",
        namespace="test",
        path="example/app",
        secrets=[
            Secret(
                name="test-values-secret",
                namespace="test",
                string_data={"some_key": "example_value"},
            ),
        ],
    )
    ks2 = Kustomization(
        name="app",
        namespace="test",
        path="example/app",
        secrets=[
            Secret(
                name="other-values-secret",
                namespace="test",
                string_data={"some_key": "example_value"},
            ),
        ],
    )
    config = ks_cluster_config([ks1, ks2])
    # Ensure can be called repeatedly
    assert len(list(config.secrets)) == 2
    assert [s.name for s in config.secrets] == [
        "test-values-secret",
        "other-values-secret",
    ]
