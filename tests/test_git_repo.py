"""Tests for git_repo."""

from pathlib import Path

from flux_local.git_repo import build_manifest

TESTDATA = Path("tests/testdata/cluster")


async def test_build_manifest() -> None:
    """Tests for building the manifest."""

    manifest = await build_manifest(TESTDATA)
    assert len(manifest.clusters) == 1
    assert manifest.clusters[0].name == "flux-system"
    assert manifest.clusters[0].path == "./tests/testdata/cluster/clusters/prod"
