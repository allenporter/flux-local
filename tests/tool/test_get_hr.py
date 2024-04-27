"""Tests for the flux-local `get hr` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["-A", "--path", "tests/testdata/cluster"]),
        (["-A", "--path", "tests/testdata/cluster2"]),
        (["-A", "--path", "tests/testdata/cluster3"]),
        (
            [
                "-A",
                "--path",
                "tests/testdata/cluster3",
                "--sources",
                "cluster=tests/testdata/cluster3",
            ]
        ),
        (["-A", "--path", "tests/testdata/cluster4"]),
        (["-A", "--path", "tests/testdata/cluster5"]),
        (["-A", "--path", "tests/testdata/cluster6"]),
        (["-A", "--path", "tests/testdata/cluster7"]),
        (["metallb", "-A", "--path", "tests/testdata/cluster"]),
        (["-n", "metallb", "--path", "tests/testdata/cluster"]),
        (["-A", "--path", "tests/testdata/cluster9/clusters/dev"]),
    ],
    ids=[
        "cluster",
        "cluster2",
        "cluster3-no-source",
        "cluster3",
        "cluster4",
        "cluster5",
        "cluster6",
        "cluster7",
        "all_namespace",
        "name",
        "cluster9",
    ],
)
async def test_get_hr(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test test get hr commands."""
    result = await run_command(["get", "hr"] + args)
    assert result == snapshot
