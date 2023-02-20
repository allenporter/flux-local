"""Tests for the flux-local command line tool."""

import pytest

from pytest_golden.plugin import GoldenTestFixture

from flux_local.command import Command, run


@pytest.mark.golden_test("testdata/*.yaml")
async def test_flux_local_golden(golden: GoldenTestFixture) -> None:
    """Test commands in golden files."""
    args = golden["args"]
    result = await run(Command(["flux-local"] + args))
    expected = golden.out.get("stdout")
    if expected:
        assert result == expected
