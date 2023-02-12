"""Tests for the flux-local command line tool."""


from pathlib import Path

from flux_local.command import Command, run

TESTDATA = Path("tests/testdata/cluster/")


async def test_build() -> None:
    """Test flux-local build command."""
    await run(Command(["flux-local", "build", str(TESTDATA)]))


async def test_build_helm() -> None:
    """Test flux-local build command with helm."""
    await run(Command(["flux-local", "build", "--enable-helm", str(TESTDATA)]))


async def test_diff() -> None:
    """Test flux-local diff command."""
    await run(Command(["flux-local", "diff", str(TESTDATA)]))


async def test_manifest() -> None:
    """Test flux-local manifest command."""
    await run(Command(["flux-local", "manifest", str(TESTDATA)]))
