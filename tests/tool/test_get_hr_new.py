"""Tests for the flux-local `get hr-new` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["--path", "tests/testdata/cluster"]),
        (["--path", "tests/testdata/cluster", "-A"]),
        (["--path", "tests/testdata/cluster", "-n", "flux-system"]),
    ],
    ids=[
        "cluster",
        "all-namespaces",
        "namespace",
    ],
)
async def test_get_hr_new(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test get hr-new command."""
    result = await run_command(["get", "hr-new"] + args)
    assert result == snapshot
