"""Tests for the flux-local `get ks` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["--path", "tests/testdata/cluster"]),
        (["--path", "tests/testdata/cluster2"]),
        (
            [
                "-A",
                "--path",
                "tests/testdata/cluster3",
                "--sources",
                "cluster=tests/testdata/cluster3",
            ]
        ),
        (["--path", "tests/testdata/cluster4"]),
        (["--path", "tests/testdata/cluster5"]),
        (["--all", "--path", "tests/testdata/cluster5"]),
        (["--path", "tests/testdata/cluster6"]),
        (["--path", "tests/testdata/cluster7"]),
        (["--path", "./tests/testdata/cluster/clusters/prod"]),
        (["--path", "tests/testdata/cluster", "-o", "wide"]),
        (["--path", "tests/testdata/cluster2", "-o", "wide"]),
        (["--all-namespaces", "--path", "./tests/testdata/cluster/apps/prod"]),
        (["--path", "tests/testdata/cluster9/clusters/dev"]),
    ],
    ids=[
        "cluster",
        "cluster2",
        "cluster3",
        "cluster4",
        "cluster5",
        "cluster5-all",
        "cluster6",
        "cluster7",
        "cluster_path",
        "wide",
        "cluster2-wide",
        "ks_path",
        "cluster9",
    ],
)
async def test_get_ks(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test test get ks commands."""
    result = await run_command(["get", "ks"] + args)
    assert result == snapshot
