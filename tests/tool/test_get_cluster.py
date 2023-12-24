"""Tests for the flux-local `get cluster` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["--path", "tests/testdata/cluster"]),
        (["--path", "tests/testdata/cluster2"]),
        (["--path", "tests/testdata/cluster3"]),
        (
            [
                "--path",
                "tests/testdata/cluster3",
                "--sources",
                "cluster=tests/testdata/cluster3",
            ]
        ),
        (["--path", "tests/testdata/cluster4"]),
        (["--path", "tests/testdata/cluster5"]),
        (["--path", "tests/testdata/cluster6"]),
        (["--path", "tests/testdata/cluster7"]),
        (["--all-namespaces", "--path", "tests/testdata/cluster/"]),
        (["--path", "tests/testdata/cluster", "-o", "yaml"]),
        (["--path", "tests/testdata/cluster", "-o", "yaml", "--enable-images"]),
        (["--path", "tests/testdata/cluster8", "-o", "yaml"]),
        (["--path", "tests/testdata/cluster8", "-o", "yaml", "--enable-images"]),
        (["--path", "tests/testdata/cluster8", "-o", "yaml", "--enable-images", "--no-skip-secrets"]),
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
        "all-namespaces",
        "yaml-cluster-no-images",
        "yaml-cluster-images",
        "yaml-cluster8-no-images",
        "yaml-cluster8-images",
        "yaml-cluster8-images-allow-secrets",
    ],
)
async def test_get_cluster(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test test get cluster commands."""
    result = await run_command(["get", "cluster"] + args)
    assert result == snapshot
