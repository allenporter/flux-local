"""Library for issuing commands using asyncio and returning the result."""

import asyncio
import logging
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_CONCURRENCY = 20
_SEM = asyncio.Semaphore(_CONCURRENCY)


# No public API
__all__: list[str] = []


class CommandException(Exception):
    """Error while running a command."""


@dataclass
class Command:
    """An instance of a command to run."""

    cmd: list[str]
    """Array of command line arguments."""

    cwd: Path | None = None
    """Current working directory."""

    @property
    def string(self) -> str:
        """Render the command as a single string."""
        return " ".join([shlex.quote(arg) for arg in self.cmd])

    def __str__(self) -> str:
        """Render as a debug string."""
        return f"({self.cwd}) {self.string}"


async def _run_piped_with_sem(cmds: list[Command]) -> str:
    """Run a set of commands, piped together, returning stdout of last."""
    stdin = None
    out = None
    for cmd in cmds:
        _LOGGER.debug("Running command: %s", cmd)
        proc = await asyncio.create_subprocess_shell(
            cmd.string,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cmd.cwd,
        )
        out, err = await proc.communicate(stdin)
        if proc.returncode:
            if out:
                _LOGGER.error(out.decode("utf-8"))
            if err:
                _LOGGER.error(err.decode("utf-8"))
            raise CommandException(
                f"Subprocess '{cmd}' failed with return code {proc.returncode}: "
                + out.decode("utf-8")
            )
        stdin = out
    return out.decode("utf-8") if out else ""


async def run_piped(cmds: list[Command]) -> str:
    """Run a set of commands, piped together, returning stdout of last."""
    async with _SEM:
        result = await _run_piped_with_sem(cmds)
    return result


async def run(cmd: Command) -> str:
    """Run the specified command and return stdout."""
    return await run_piped([cmd])
