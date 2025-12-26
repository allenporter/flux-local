"""Tests for the flux-local `build` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["--path=tests/testdata/cluster/"]),
        (["apps", "--path=tests/testdata/cluster/"]),
        (["apps", "--path=tests/testdata/cluster6/"]),
        (["apps", "--path=tests/testdata/cluster8/"]),
        (["apps-stack", "--path=tests/testdata/cluster9/clusters/dev"]),
        (["--path=tests/testdata/cluster10", "--skip-invalid-kustomization-paths"]),
        (["--path=tests/testdata/cluster11", "--sources", "flux-system,apps=tests/testdata/cluster11/externalartifact"]),
    ],
    ids=[
        "build-ks",
        "build-ks-single",
        "build-ks-single-cluster6",
        "build-ks-single-cluster8",
        "build-ks-single-cluster9",
        "build-ks-cluster10-invalid-paths",
        "build-ks-cluster11-external-artifact",
    ],
)
async def test_build_ks(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test build commands."""
    result = await run_command(["build", "ks"] + args)
    assert result == snapshot


@pytest.mark.parametrize(
    ("args"),
    [
        (["--path=tests/testdata/cluster/"]),
        (["apps", "--path=tests/testdata/cluster/"]),
        (["apps", "--path=tests/testdata/cluster6/"]),
        (["apps", "--path=tests/testdata/cluster8/"]),
        (["apps-stack", "--path=tests/testdata/cluster9/clusters/dev"]),
        # This --bootstrap_repo_name case is not fully implemented yet
        # (["--path=tests/testdata/cluster10", "--bootstrap_repo_name=flux-system"]),
    ],
    ids=[
        "build-ks",
        "build-ks-single",
        "build-ks-single-cluster6",
        "build-ks-single-cluster8",
        "build-ks-single-cluster9",
        # "build-ks-cluster10-invalid-paths",
    ],
)
async def test_build_ks_new(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test build commands."""
    result = await run_command(["build", "ks-new"] + args)
    assert result == snapshot
