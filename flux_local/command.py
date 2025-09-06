"""Library for issuing commands using asyncio and returning the result."""

import asyncio
from abc import ABC, abstractmethod
import logging
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
import os

from .exceptions import CommandException

_LOGGER = logging.getLogger(__name__)

_CONCURRENCY = 20
_SEM = asyncio.Semaphore(_CONCURRENCY)
_TIMEOUT = 20.0


# No public API
__all__: list[str] = []


class Task(ABC):
    """An instance of a async task to execute."""

    @abstractmethod
    async def run(self, stdin: bytes | None = None) -> bytes:
        """Execute the task and return the result."""


def format_path(path: Path) -> str:
    """Format path for debugging."""
    if path.is_absolute():
        cwd = Path.cwd()
        if path.is_relative_to(cwd):
            rel_path = str(path.relative_to(cwd))
            return f"{rel_path} (abs)"
    return str(path)


@dataclass
class Command(Task):
    """An instance of a command to run."""

    cmd: list[str]
    """Array of command line arguments."""

    cwd: Path | None = None
    """Current working directory."""

    exc: type[CommandException] = CommandException
    """Exception to throw in case of an error."""

    retcodes: list[int] | None = None
    """Non-zero error codes that are allowed to indicate success (e.g. for diff)."""

    env: dict[str, str] | None = None
    """Environment variables for the subprocess."""

    max_retries: int = 3
    """Maximum number of retries on timeout or failure."""

    retry_delay: float = 5.0
    """Delay between retries in seconds."""

    @property
    def string(self) -> str:
        """Render the command as a single string."""
        return " ".join([shlex.quote(arg) for arg in self.cmd])

    def __str__(self) -> str:
        """Render as a debug string."""
        cwd: str = ""
        if self.cwd:
            cwd = f"({format_path(self.cwd)}) "
        return f"{cwd}{self.string}"

    async def run(self, stdin: bytes | None = None) -> bytes:
        """Run the command, returning stdout."""
        for attempt in range(self.max_retries + 1):
            try:
                _LOGGER.debug(
                    "Running command (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    self,
                )
                env = {
                    **os.environ,
                    **(self.env if self.env else {}),
                }

                async with asyncio.timeout(_TIMEOUT):
                    proc = await asyncio.create_subprocess_shell(
                        self.string,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=self.cwd,
                        env=env,
                    )
                    out, err = await proc.communicate(stdin)

                if proc.returncode:
                    if self.retcodes and proc.returncode in self.retcodes:
                        return out
                    errors = [f"Command '{self}' failed with return code {proc.returncode}"]
                    if out:
                        errors.append(out.decode("utf-8"))
                    if err:
                        errors.append(err.decode("utf-8"))
                    error_msg = "\n".join(errors)
                    _LOGGER.debug("%s", error_msg)

                    if attempt < self.max_retries:
                        _LOGGER.warning(
                            "Command failed (attempt %d/%d), retrying in %s seconds: %s",
                            attempt + 1,
                            self.max_retries + 1,
                            self.retry_delay,
                            error_msg.split('\n')[0],
                        )
                        await asyncio.sleep(self.retry_delay)
                        continue

                    raise self.exc(error_msg)
                return out

            except asyncio.TimeoutError as err:
                if attempt < self.max_retries:
                    _LOGGER.warning(
                        "Command timed out (attempt %d/%d), retrying in %s seconds: %s",
                        attempt + 1,
                        self.max_retries + 1,
                        self.retry_delay,
                        self,
                    )
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise self.exc(f"Command '{self}' timed out after {self.max_retries + 1} attempts") from err

async def _run_piped_with_sem(cmds: Sequence[Task]) -> str:
    """Run a set of commands, piped together, returning stdout of last."""
    stdin = None
    out = None
    for cmd in cmds:
        out = await cmd.run(stdin)
        stdin = out
    return out.decode("utf-8") if out else ""

async def run_piped(cmds: Sequence[Task]) -> str:
    """Run a set of commands, piped together, returning stdout of last."""
    async with _SEM:
        result = await _run_piped_with_sem(cmds)
    return result


async def run(cmd: Task) -> str:
    """Run the specified command and return stdout."""
    return await run_piped([cmd])
