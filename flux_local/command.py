"""Library for issuing commands using asyncio and returning the result."""

import asyncio
import logging
import shlex
import subprocess

_LOGGER = logging.getLogger(__name__)


class CommandException(Exception):
    """Error while running a command."""


async def run_piped(cmds: list[list[str]]) -> str:
    """Run a set of commands, piped together, returning stdout of last."""
    stdin = None
    out = None
    for cmd in cmds:
        cmd_text = " ".join([shlex.quote(arg) for arg in cmd])
        _LOGGER.debug("Running command: %s", cmd_text)
        proc = await asyncio.create_subprocess_shell(
            cmd_text,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = await proc.communicate(stdin)
        if proc.returncode:
            if out:
                _LOGGER.error(out.decode("utf-8"))
            if err:
                _LOGGER.error(err.decode("utf-8"))
            raise CommandException(
                f"Subprocess '{cmd_text}' failed with return code {proc.returncode}: "
                + out.decode("utf-8")
            )
        stdin = out
    return out.decode("utf-8") if out else ""


async def run(cmd: list[str]) -> str:
    """Run the specified command and return stdout."""
    return await run_piped([cmd])
