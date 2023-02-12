"""Tests for the flux-local command line tool."""

import pytest

from flux_local.command import Command, run

TESTDATA = "tests/testdata/cluster/"


@pytest.mark.parametrize(
    "args",
    [
        ["flux-local", "build", TESTDATA],
        ["flux-local", "build", "--enable-helm", TESTDATA],
        ["flux-local", "diff", TESTDATA],
        ["flux-local", "manifest", TESTDATA],
        ["flux-local", "test", TESTDATA],
        ["flux-local", "get", "ks"],
        ["flux-local", "get", "hr"],
        ["flux-local", "get", "hr", "-n", "metallb"],
    ],
)
async def test_flux_local_command(args: list[str]) -> None:
    """Test flux-local build command."""
    await run(Command(args))
