"""Tests for kustomize library."""

from pathlib import Path

from flux_local.kustomize import Kustomize

TESTDATA_DIR = Path("tests/testdata")


async def test_build() -> None:
    """Test a kustomize build command."""
    result = await Kustomize.build(TESTDATA_DIR / "repo").run()
    assert "Secret" in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/all.golden").read_text()


async def test_grep() -> None:
    """Test a kustomize build command."""
    result = await Kustomize.build(TESTDATA_DIR / "repo").grep("kind=ConfigMap").run()
    assert "Secret" not in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo/configmap.golden").read_text()


async def test_objects() -> None:
    """Test loading yaml documents."""
    kustomize = Kustomize.build(TESTDATA_DIR / "repo").grep("kind=ConfigMap")
    result = await kustomize.objects()
    assert len(result) == 1
    assert result[0].get("kind") == "ConfigMap"
    assert result[0].get("apiVersion") == "v1"
