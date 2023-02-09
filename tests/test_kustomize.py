"""Tests for kustomize library."""

from pathlib import Path

import pytest

from flux_local import command, kustomize

TESTDATA_DIR = Path("tests/testdata")


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
        command.CommandException, match="require-test-annotation: validation error"
    ):
        await cmd.validate(TESTDATA_DIR / "policies/fail.yaml")
