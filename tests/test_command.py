"""Tests for command library."""

import pytest

from flux_local.command import Command, run, run_piped
from flux_local.exceptions import CommandException


async def test_command() -> None:
    """Test stdout parsing of a command."""
    result = await run(Command(["echo", "Hello"]))
    assert result == "Hello\n"


async def test_run_piped_command() -> None:
    """Test running commands piped together."""
    result = await run_piped(
        [
            Command(["echo", "Hello"]),
            Command(["sed", "s/Hello/Goodbye/"]),
        ]
    )
    assert result == "Goodbye\n"


async def test_failed_command() -> None:
    """Test a failing command."""
    with pytest.raises(CommandException, match="return code 1"):
        await run(Command(["/bin/false"]))
