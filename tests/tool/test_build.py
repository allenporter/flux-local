"""Tests for the flux-local `build` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["tests/testdata/cluster/"]),
        (["tests/testdata/cluster2/"]),
        (["tests/testdata/cluster3/"]),
        (["tests/testdata/cluster6/"]),
        (["--enable-helm", "--skip-crds", "tests/testdata/cluster/"]),
        (
            [
                "--enable-helm",
                "--skip-crds",
                "tests/testdata/cluster6/",
                "-a",
                "batch/v1/CronJob",
            ]
        ),
        (["--enable-helm", "--no-skip-secrets", "tests/testdata/cluster8/"]),
    ],
    ids=[
        "build",
        "build-cluster2",
        "build-cluster3",
        "build-cluster6",
        "build-helm-cluster",
        "build-helm-cluster6",
        "build-helm-cluster8-valuesFrom"
    ],
)
async def test_build(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test build commands."""
    result = await run_command(["build"] + args)
    assert result == snapshot
