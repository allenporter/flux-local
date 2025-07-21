"""Tests for the flux-local `build` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["--path=tests/testdata/cluster/"]),
        (["weave-gitops", "--path=tests/testdata/cluster/"]),
        (
            [
                "-A",
                "--path",
                "tests/testdata/cluster3",
                "--sources",
                "cluster=tests/testdata/cluster3",
            ]
        ),
        (
            [
                "renovate",
                "-A",
                "--skip-crds",
                "--path",
                "tests/testdata/cluster6/",
                "-a",
                "batch/v1/CronJob",
            ]
        ),
        (["podinfo", "-n", "podinfo", "--path=tests/testdata/cluster8/"]),
        (["podinfo", "-n", "default", "--path=tests/testdata/cluster9/clusters/dev"]),
    ],
    ids=[
        "build-hr",
        "build-hr-single",
        "build-hr-cluster3",
        "build-hr-single-cluster6",
        "build-hr-single-cluster8",
        "build-hr-single-cluster9",
    ],
)
async def test_build_hr(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test build commands."""
    result = await run_command(["build", "hr"] + args)
    assert result == snapshot
