"""Tests for kustomize library."""

from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion
import yaml

from flux_local import kustomize, exceptions, manifest, command

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


INVALID_YAML = """
---
foo: !bar
"""


async def test_objects_failure() -> None:
    """Test loading yaml documents."""

    class FakeTask(command.Task):
        async def run(self, stdin: bytes | None = None) -> bytes:
            """Execute the task and return the result."""
            return INVALID_YAML.encode()

    cmd = kustomize.Kustomize([FakeTask()])
    with pytest.raises(
        exceptions.KustomizeException,
        match=r"Unable to parse.*could not determine a constructor",
    ):
        await cmd.objects()


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


async def test_flux_build_path_is_not_dir() -> None:
    """Test case where the flux build path does not exist."""
    cmd = kustomize.flux_build(
        manifest.Kustomization(name="example", path="./", namespace="flux-system"),
        Path(TESTDATA_DIR) / "does-not-exist",
    )
    with pytest.raises(exceptions.FluxException, match="not a directory"):
        await cmd.objects()


async def test_flux_build() -> None:
    """Test flux build cli."""
    docs = list(
        yaml.safe_load_all(
            Path(
                f"{TESTDATA_DIR}/cluster/clusters/prod/flux-system/gotk-sync.yaml"
            ).read_text()
        )
    )
    assert len(docs) == 2
    ks = manifest.Kustomization.parse_doc(docs[1])
    cmd = kustomize.flux_build(ks, Path(ks.path))
    result = await cmd.run()
    assert "GitRepository" in result
