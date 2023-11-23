"""Tests for kustomize library."""

from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from flux_local import kustomize, exceptions

TESTDATA_DIR = Path("tests/testdata")

KUSTOMIZATION = """---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- example.yaml
"""


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_grep(path: Path, snapshot: SnapshotAssertion) -> None:
    """Test a kustomize grep command."""
    result = await kustomize.grep("kind=ConfigMap", path).run()
    assert "Secret" not in result
    assert "ConfigMap" in result
    assert result == snapshot


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_objects(path: Path, snapshot: SnapshotAssertion) -> None:
    """Test loading yaml documents."""
    cmd = kustomize.grep("kind=ConfigMap", path)
    result = await cmd.objects()
    assert len(result) == 1
    assert result[0].get("kind") == "ConfigMap"
    assert result[0].get("apiVersion") == "v1"
    assert result == snapshot


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_stash(path: Path) -> None:
    """Test loading yaml documents."""
    cmd = await kustomize.grep("kind=Ignored", path, invert=True).stash()
    result = await cmd.grep("kind=ConfigMap").objects()
    assert len(result) == 1
    assert result[0].get("kind") == "ConfigMap"
    assert result[0].get("apiVersion") == "v1"
    result = await cmd.grep("kind=Secret").objects()
    assert len(result) == 1
    assert result[0].get("kind") == "Secret"
    assert result[0].get("apiVersion") == "v1"
    result = await cmd.grep("kind=Unknown").objects()
    assert len(result) == 0


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_validate_pass(path: Path) -> None:
    """Test applying policies to validate resources."""
    cmd = kustomize.grep("kind=ConfigMap", path)
    await cmd.validate(TESTDATA_DIR / "policies/pass.yaml")


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_validate_fail(path: Path) -> None:
    """Test applying policies to validate resources."""
    cmd = kustomize.grep("kind=ConfigMap", path)
    with pytest.raises(
        exceptions.CommandException, match="require-test-annotation: validation error"
    ):
        await cmd.validate(TESTDATA_DIR / "policies/fail.yaml")


async def test_target_namespace() -> None:
    """Test a kustomization with a target namespace."""
    ks = kustomize.grep("kind=ConfigMap", TESTDATA_DIR / "repo")

    result = await ks.objects()
    assert len(result) == 1
    config_map = result[0]
    assert "metadata" in config_map
    assert config_map["metadata"] == {
        "name": "cluster-settings",
        "namespace": "flux-system",
        "annotations": {
            "config.kubernetes.io/index": "0",
            "config.kubernetes.io/path": "cluster-settings.yaml",
            "internal.config.kubernetes.io/index": "0",
            "internal.config.kubernetes.io/path": "cluster-settings.yaml",
        },
    }

    result = await ks.objects(target_namespace="configs")
    assert len(result) == 1
    config_map = result[0]
    assert "metadata" in config_map
    assert config_map["metadata"] == {
        "name": "cluster-settings",
        # Verify updated namespace
        "namespace": "configs",
        "annotations": {
            "config.kubernetes.io/index": "0",
            "config.kubernetes.io/path": "cluster-settings.yaml",
            "internal.config.kubernetes.io/index": "0",
            "internal.config.kubernetes.io/path": "cluster-settings.yaml",
        },
    }
