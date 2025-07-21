"""Tests for the flux-local `build` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("cmd"),
    [
        "ks",
        "ks-new",
    ],
)
@pytest.mark.parametrize(
    ("args"),
    [
        (["--path=tests/testdata/cluster/"]),
        (["apps", "--path=tests/testdata/cluster/"]),
        (["apps", "--path=tests/testdata/cluster6/"]),
        (["apps", "--path=tests/testdata/cluster8/"]),
        (["apps-stack", "--path=tests/testdata/cluster9/clusters/dev"]),
    ],
    ids=[
        "build-ks",
        "build-ks-single",
        "build-ks-single-cluster6",
        "build-ks-single-cluster8",
        "build-ks-single-cluster9",
    ],
)
async def test_build_ks(args: list[str], cmd: str, snapshot: SnapshotAssertion) -> None:
    """Test build commands."""
    result = await run_command(["build", cmd] + args)
    assert result == snapshot
