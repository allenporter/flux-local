"""Tests for the flux-local command line tool."""


from pathlib import Path

from flux_local.command import Command, run

TESTDATA = Path("tests/testdata/cluster/")


async def test_flux_local_build() -> None:
    """Test flux-local build command."""
    await run(Command(["flux-local", "build", str(TESTDATA)]))
