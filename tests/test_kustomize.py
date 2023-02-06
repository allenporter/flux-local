"""Tests for kustomize library."""

from pathlib import Path

import pytest

from flux_local import command, kustomize

TESTDATA_DIR = Path("tests/testdata")


async def test_build() -> None:
    """Test a kustomize build command."""
    result = await kustomize.build(TESTDATA_DIR / "repo").run()
    assert "Secret" in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/all.golden").read_text()


async def test_build_grep() -> None:
    """Test a kustomize build and grep command chained."""
    result = await kustomize.build(TESTDATA_DIR / "repo").grep("kind=ConfigMap").run()
    assert "Secret" not in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/configmap.build.golden").read_text()


async def test_grep() -> None:
    """Test a kustomize grep command."""
    result = await kustomize.grep("kind=ConfigMap", TESTDATA_DIR / "repo").run()
    assert "Secret" not in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/configmap.grep.golden").read_text()


async def test_objects() -> None:
    """Test loading yaml documents."""
    cmd = kustomize.build(TESTDATA_DIR / "repo").grep("kind=ConfigMap")
    result = await cmd.objects()
    assert len(result) == 1
    assert result[0].get("kind") == "ConfigMap"
    assert result[0].get("apiVersion") == "v1"


async def test_validate_pass() -> None:
    """Test applying policies to validate resources."""
    cmd = kustomize.build(TESTDATA_DIR / "repo")
    await cmd.validate(TESTDATA_DIR / "policies/pass.yaml")


async def test_validate_fail() -> None:
    """Test applying policies to validate resources."""
    cmd = kustomize.build(TESTDATA_DIR / "repo")
    with pytest.raises(
        command.CommandException, match="require-test-annotation: validation error"
    ):
        await cmd.validate(TESTDATA_DIR / "policies/fail.yaml")
