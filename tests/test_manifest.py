"""Tests for manifest library."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from flux_local.manifest import (
    Cluster,
    GitRepositoryRef,
    HelmRelease,
    Kustomization,
    ConfigMap,
    RawObject,
    parse_raw_obj,
    HelmRepository,
    Manifest,
    NamedResource,
    OCIRepository,
    read_manifest,
    write_manifest,
    strip_resource_attributes,
)

TESTDATA_DIR = Path("tests/testdata/cluster/infrastructure")
TEST_PODINFO_HELMRELEASE = Path("tests/testdata/cluster8/apps/podinfo.yaml")


def test_parse_helm_release() -> None:
    """Test parsing a helm release doc."""

    release = HelmRelease.parse_doc(
        yaml.load(
            (TESTDATA_DIR / "controllers/metallb-release.yaml").read_text(),
            Loader=yaml.CLoader,
        )
    )
    assert release.name == "metallb"
    assert release.namespace == "metallb"
    assert release.chart.name == "metallb"
    assert release.chart.version == "4.1.14"
    assert release.chart.repo_name == "bitnami"
    assert release.chart.repo_namespace == "flux-system"
    assert release.values


def test_compact_helm_release() -> None:
    """Test parsing a helm release doc."""

    release = HelmRelease.parse_doc(
        yaml.load(
            (TESTDATA_DIR / "controllers/metallb-release.yaml").read_text(),
            Loader=yaml.CLoader,
        )
    )
    print(release)
    assert release.compact_dict() == {
        "name": "metallb",
        "namespace": "metallb",
        "chart": {
            "name": "metallb",
            "repo_kind": "HelmRepository",
            "repo_name": "bitnami",
            "repo_namespace": "flux-system",
        },
    }


def test_parse_helm_repository() -> None:
    """Test parsing a helm repository doc."""

    docs = list(
        yaml.load_all(
            (TESTDATA_DIR / "configs/helm-repositories.yaml").read_text(),
            Loader=yaml.CLoader,
        )
    )
    assert len(docs) == 3
    helm_repo = HelmRepository.parse_doc(docs[0])
    assert helm_repo.name == "bitnami"
    assert helm_repo.namespace == "flux-system"
    assert helm_repo.url == "https://charts.bitnami.com/bitnami"
    oci_repo = OCIRepository.parse_doc(docs[1])
    assert oci_repo.name == "podinfo"
    assert oci_repo.namespace == "flux-system"
    assert oci_repo.url == "oci://ghcr.io/stefanprodan/charts"


def test_git_repository_ref_str() -> None:
    """Test GitRepositoryRef ref_str property."""
    # Test with commit
    ref = GitRepositoryRef(commit="abc123")
    assert ref.ref_str == "commit:abc123"

    # Test with tag
    ref = GitRepositoryRef(tag="v1.0.0")
    assert ref.ref_str == "tag:v1.0.0"

    # Test with branch
    ref = GitRepositoryRef(branch="main")
    assert ref.ref_str == "branch:main"

    # Test with semver
    ref = GitRepositoryRef(semver="1.0.0")
    assert ref.ref_str == "semver:1.0.0"

    # Test with no references
    ref = GitRepositoryRef()
    assert ref.ref_str is None

    # Test with multiple references (should use first non-None)
    ref = GitRepositoryRef(commit="abc123", tag="v1.0.0")
    assert ref.ref_str == "commit:abc123"


async def test_write_manifest_file() -> None:
    """Test reading an invalid manifest file."""
    await write_manifest(Path("/dev/null"), Manifest(clusters=[]))


async def test_read_write_empty_manifest(tmp_path: Path) -> None:
    """Test serializing and reading back a manifest."""
    manifest = Manifest(clusters=[])
    await write_manifest(tmp_path / "file.yaml", manifest)
    new_manifest = await read_manifest(tmp_path / "file.yaml")
    assert not new_manifest.clusters


async def test_read_manifest_invalid_file() -> None:
    """Test reading an invalid manifest file."""
    with pytest.raises(ValueError, match="validation error for Manifest"):
        await read_manifest(Path("/dev/null"))


async def test_serializing_manifest(tmp_path: Path) -> None:
    """Test serializing a manifest to a dictionary."""
    manifest = Manifest(
        clusters=[
            Cluster(
                path="./example",
                kustomizations=[],
            )
        ]
    )
    await write_manifest(tmp_path / "file.yaml", manifest)
    new_manifest = await read_manifest(tmp_path / "file.yaml")
    assert new_manifest.compact_dict() == {
        "clusters": [
            {
                "path": "./example",
                "kustomizations": [],
            },
        ]
    }


def test_parse_helmrelease_chartref() -> None:
    """Test parsing a helm release doc."""

    HELM_CHARTREF_FILE = Path("tests/testdata/cluster9/apps/podinfo/podinfo.yaml")
    docs = list(
        yaml.load_all(
            HELM_CHARTREF_FILE.read_text(),
            Loader=yaml.CLoader,
        )
    )
    assert len(docs) == 1
    assert docs[0].get("kind") == "HelmRelease"

    release = HelmRelease.parse_doc(docs[0])
    assert release.name == "podinfo"
    assert release.namespace == "default"
    assert release.chart.name == "podinfo"
    assert release.chart.version is None
    assert release.chart.repo_name == "podinfo"
    assert release.chart.repo_namespace == "default"
    assert release.values


def test_parse_raw_obj() -> None:
    """Test parsing raw objects into BaseManifest."""
    # Test parsing a Kustomization
    kustomization = {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {"name": "app", "namespace": "default"},
        "spec": {
            "interval": "10m",
            "targetNamespace": "default",
            "sourceRef": {
                "kind": "GitRepository",
                "name": "git-repo",
                "namespace": "test-ns",
            },
            "path": "./app",
        },
    }
    parsed = parse_raw_obj(kustomization)
    assert isinstance(parsed, Kustomization)
    assert parsed.name == "app"
    assert parsed.namespace == "default"
    assert parsed.path == "./app"

    # Test parsing a ConfigMap
    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "app-config", "namespace": "default"},
        "data": {"key": "value"},
    }
    parsed = parse_raw_obj(configmap)
    assert isinstance(parsed, ConfigMap)
    assert parsed.name == "app-config"
    assert parsed.namespace == "default"
    assert parsed.data == {"key": "value"}


def test_parse_raw_doc() -> None:
    """Test parsing a raw YAML document into RawObject."""
    yaml_doc = """
apiVersion: v1
kind: UnknownKind
metadata:
  name: my-object
  namespace: default
spec:
  someField: value
"""
    doc = yaml.load(yaml_doc, Loader=yaml.CLoader)
    parsed = parse_raw_obj(doc)
    assert isinstance(parsed, RawObject)
    assert parsed.kind == "UnknownKind"
    assert parsed.api_version == "v1"
    assert parsed.name == "my-object"
    assert parsed.namespace == "default"
    assert parsed.spec == {"someField": "value"}


def test_helmrelease_dependencies() -> None:
    """Test parsing a helm release doc."""

    docs = list(
        yaml.load_all(
            TEST_PODINFO_HELMRELEASE.read_text(),
            Loader=yaml.CLoader,
        )
    )
    assert len(docs) == 2
    release = HelmRelease.parse_doc(docs[1])
    assert release.name == "podinfo"
    assert release.namespace == "podinfo"
    # Assert on all the input dependencies that might cause HelmRelease
    # output to change. This means the HelmRepository, and any input
    # values used for substitute references.
    assert release.resource_dependencies == [
        NamedResource(kind="HelmRelease", name="podinfo", namespace="podinfo"),
        NamedResource(kind="HelmRepository", name="podinfo", namespace="flux-system"),
        NamedResource(kind="ConfigMap", name="podinfo-values", namespace="podinfo"),
        NamedResource(
            kind="Secret", name="dot-notated-target-path", namespace="podinfo"
        ),
        NamedResource(
            kind="Secret", name="escape-special-chars-path", namespace="podinfo"
        ),
        NamedResource(kind="Secret", name="podinfo-tls-values", namespace="podinfo"),
    ]


STRIP_ATTRIBUTES = [
    "app.kubernetes.io/version",
    "chart",
]


@pytest.mark.parametrize(
    ("metadata", "expected_metadata"),
    [
        (
            {
                "labels": {
                    "app.kubernetes.io/version": "1.0.0",
                    "app.kubernetes.io/managed-by": "Helm",
                }
            },
            {
                "labels": {
                    "app.kubernetes.io/managed-by": "Helm",
                },
            },
        ),
        (
            {
                "annotations": {
                    "app.kubernetes.io/version": "1.0.0",
                    "app.kubernetes.io/managed-by": "Helm",
                }
            },
            {
                "annotations": {
                    "app.kubernetes.io/managed-by": "Helm",
                },
            },
        ),
        (
            {},
            {},
        ),
    ],
)
def test_strip_resource_attributes(
    metadata: dict[str, Any], expected_metadata: dict[str, Any]
) -> None:
    """Test the strip_resource_attributes function."""
    resource = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "my-configmap",
            "namespace": "default",
            **metadata,
        },
        "data": {
            "key1": "value1",
            "key2": "value2",
        },
    }
    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert resource == {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "my-configmap",
            "namespace": "default",
            **expected_metadata,
        },
        "data": {
            "key1": "value1",
            "key2": "value2",
        },
    }


def test_strip_deployment_metadata() -> None:
    """Test the strip_resource_attributes function."""
    resource = yaml.load(
        """apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-deployment
  labels:
    app: nginx
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
        app.kubernetes.io/version: 1.0.0
    spec:
      containers:
      - name: nginx
        image: nginx:1.14.2
        ports:
        - containerPort: 80
""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-deployment
  labels:
    app: nginx
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
      - name: nginx
        image: nginx:1.14.2
        ports:
        - containerPort: 80
"""
    )


def test_strip_list_metadata() -> None:
    """Test the stripping metadata from a list resource."""
    resource = yaml.load(
        """apiVersion: v1
items:
- apiVersion: stable.example.com/v1
  kind: CronTab
  metadata:
    annotations:
      app: my-cron-tab
      app.kubernetes.io/version: 1.0.0
    creationTimestamp: '2021-06-20T07:35:27Z'
    generation: 1
    name: my-new-cron-object
    namespace: default
    resourceVersion: '1326'
    uid: 9aab1d66-628e-41bb-a422-57b8b3b1f5a9
  spec:
    cronSpec: '* * * * */5'
    image: my-awesome-cron-image
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''

""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: v1
items:
- apiVersion: stable.example.com/v1
  kind: CronTab
  metadata:
    annotations:
      app: my-cron-tab
    creationTimestamp: '2021-06-20T07:35:27Z'
    generation: 1
    name: my-new-cron-object
    namespace: default
    resourceVersion: '1326'
    uid: 9aab1d66-628e-41bb-a422-57b8b3b1f5a9
  spec:
    cronSpec: '* * * * */5'
    image: my-awesome-cron-image
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
"""
    )


def test_strip_list_null_items() -> None:
    """Test corner cases of handling metadata."""
    resource = yaml.load(
        """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items:

""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items: null
"""
    )


def test_strip_list_item_without_metdata() -> None:
    """Test corner cases of handling metadata."""
    resource = yaml.load(
        """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items:
- kind: CronTab
""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items:
- kind: CronTab
"""
    )
