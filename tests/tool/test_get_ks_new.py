"""Tests for the flux-local `get ks-new` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["--path", "tests/testdata/cluster"]),
        (["--path", "tests/testdata/cluster", "-o", "wide"]),
    ],
    ids=[
        "cluster",
        "wide",
    ],
)
async def test_get_ks_new(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test get ks-new command."""
    result = await run_command(["get", "ks-new"] + args)
    assert result == snapshot
