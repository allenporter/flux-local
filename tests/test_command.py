"""Tests for command library."""

import pytest

from flux_local import command


async def test_command() -> None:
    """Test stdout parsing of a command."""
    result = await command.run(["echo", "Hello"])
    assert result == "Hello\n"


async def test_run_piped_command() -> None:
    """Test running commands piped together."""
    result = await command.run_piped(
        [
            ["echo", "Hello"],
            ["sed", "s/Hello/Goodbye/"],
        ]
    )
    assert result == "Goodbye\n"


async def test_failed_command() -> None:
    """Test a failing command."""
    with pytest.raises(command.CommandException, match="return code 1"):
        await command.run(["/bin/false"])
