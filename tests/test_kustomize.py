"""Tests for kustomize library."""

from pathlib import Path

import pytest

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
async def test_build(path: Path) -> None:
    """Test a kustomize build command."""
    result = await kustomize.build(path).run()
    assert "Secret" in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/all.golden").read_text()


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_build_grep(path: Path) -> None:
    """Test a kustomize build and grep command chained."""
    result = await kustomize.build(path).grep("kind=ConfigMap").run()
    assert "Secret" not in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/configmap.build.golden").read_text()


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_grep(path: Path) -> None:
    """Test a kustomize grep command."""
    result = await kustomize.grep("kind=ConfigMap", path).run()
    assert "Secret" not in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/configmap.grep.golden").read_text()


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_objects(path: Path) -> None:
    """Test loading yaml documents."""
    cmd = kustomize.build(path).grep("kind=ConfigMap")
    result = await cmd.objects()
    assert len(result) == 1
    assert result[0].get("kind") == "ConfigMap"
    assert result[0].get("apiVersion") == "v1"


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_stash(path: Path) -> None:
    """Test loading yaml documents."""
    cmd = await kustomize.build(path).stash()
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
    cmd = kustomize.build(path)
    await cmd.validate(TESTDATA_DIR / "policies/pass.yaml")


@pytest.mark.parametrize(
    "path",
    [TESTDATA_DIR / "repo", (TESTDATA_DIR / "repo").absolute()],
)
async def test_validate_fail(path: Path) -> None:
    """Test applying policies to validate resources."""
    cmd = kustomize.build(path)
    with pytest.raises(
        exceptions.CommandException, match="require-test-annotation: validation error"
    ):
        await cmd.validate(TESTDATA_DIR / "policies/fail.yaml")


async def test_cannot_kustomize(tmp_path: Path) -> None:
    """Test that empty directories cannot be kustomized."""
    assert not await kustomize.can_kustomize_dir(tmp_path)


async def test_can_kustomize(tmp_path: Path) -> None:
    """Test that empty directories cannot be kustomized."""
    ks = tmp_path / "kustomization.yaml"
    ks.write_text(KUSTOMIZATION)
    assert await kustomize.can_kustomize_dir(tmp_path)


async def test_fluxtomize_file(tmp_path: Path) -> None:
    """Test implicit kustomization of files in a directory."""
    settings = (TESTDATA_DIR / "repo/cluster-settings.yaml").read_text()
    example_yaml = tmp_path / "example.yaml"
    example_yaml.write_text(settings)

    content = await kustomize.fluxtomize(tmp_path)
    assert content
    assert content.decode("utf-8").split("\n") == [
        "---",
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        "  namespace: flux-system",
        "  name: cluster-settings",
        "data:",
        "  CLUSTER: dev",
        "  DOMAIN: example.org",
        "",
    ]


async def test_fluxtomize_subdir(tmp_path: Path) -> None:
    """Test implicit kustomization of subdirectories that can be kustomized."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    ks = subdir / "kustomization.yaml"
    ks.write_text(KUSTOMIZATION)

    settings = (TESTDATA_DIR / "repo/cluster-settings.yaml").read_text()
    example_yaml = subdir / "example.yaml"
    example_yaml.write_text(settings)

    content = await kustomize.fluxtomize(tmp_path)
    assert content
    assert content.decode("utf-8").split("\n") == [
        "---",
        "apiVersion: v1",
        "data:",
        "  CLUSTER: dev",
        "  DOMAIN: example.org",
        "kind: ConfigMap",
        "metadata:",
        "  name: cluster-settings",
        "  namespace: flux-system",
        "",
    ]


async def test_fluxtomize_ignores_empty_subdir(tmp_path: Path) -> None:
    """Test implicit kustomization."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    content = await kustomize.fluxtomize(tmp_path)
    assert not content


async def test_build_flags() -> None:
    """Test a kustomize build command with extra flags."""
    result = await kustomize.build(
        TESTDATA_DIR / "repo",
        # Duplicates existing flags, should be a no-op
        kustomize_flags=["--load-restrictor=LoadRestrictionsNone"],
    ).run()
    assert "Secret" in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/all.golden").read_text()


async def test_target_namespace() -> None:
    """Test a kustomization with a target namespace."""
    ks = kustomize.build(TESTDATA_DIR / "repo").grep("kind=ConfigMap")

    result = await ks.objects()
    assert len(result) == 1
    config_map = result[0]
    assert "metadata" in config_map
    assert config_map["metadata"] == {
        "name": "cluster-settings",
        "namespace": "flux-system",
        "annotations": {
            "config.kubernetes.io/index": "0",
            "internal.config.kubernetes.io/index": "0",
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
            "internal.config.kubernetes.io/index": "0",
        },
    }
