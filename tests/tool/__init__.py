"""Test helpers for flux-local tools."""

from flux_local.command import Command, run

FLUX_LOCAL_BIN = "flux-local"


async def run_command(args: list[str], env: dict[str, str] | None = None) -> str:
    return await run(Command([FLUX_LOCAL_BIN] + args, env=env))
