"""Flux-local shell command implementation."""

import asyncio
import logging
import sys
from argparse import _SubParsersAction as SubParsersAction, ArgumentParser
from pathlib import Path
from typing import Any, cast

from flux_local.store import InMemoryStore
from flux_local.orchestrator import Orchestrator, BootstrapOptions
from flux_local.exceptions import FluxException

from .repl import FluxShell

_LOGGER = logging.getLogger(__name__)


class ShellAction:
    """Flux-local shell action."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the shell subcommand."""
        parser = cast(
            ArgumentParser,
            subparsers.add_parser(
                "shell",
                help="Start an interactive shell",
                description="Start an interactive shell for exploring flux resources. This is an experimental command.",
            ),
        )
        parser.add_argument(
            "--path",
            type=str,
            help="Path to the directory containing flux manifests",
            default=".",
        )
        parser.set_defaults(cls=cls)
        return parser

    async def run(self, **kwargs: Any) -> None:
        """Run the interactive shell with bootstrap support."""
        # Get the path from arguments or use current directory
        path = Path(kwargs.get("path", ".")).resolve()
        _LOGGER.info("Starting shell with path: %s", path)

        # Initialize store and orchestrator
        store = InMemoryStore()
        orchestrator = Orchestrator(store=store)
        bootstrap_options = BootstrapOptions(path=path)

        try:
            # Bootstrap the system with the specified path
            task = asyncio.create_task(orchestrator.bootstrap(bootstrap_options))
            _LOGGER.debug("Orchestrator started, bootstrapping with path: %s", path)

            # Create the shell with the bootstrapped store
            shell = FluxShell(store=store, path=str(path))
            _LOGGER.info("Interactive shell ready. Type 'help' for available commands.")

            # Run the shell in the event loop
            try:
                await asyncio.get_event_loop().run_in_executor(None, shell.cmdloop)
            except KeyboardInterrupt:
                print("\nUse 'exit' or 'quit' to exit the shell", file=sys.stderr)

        except FluxException as e:
            _LOGGER.error("Flux error: %s", e)
            raise
        except Exception as e:
            _LOGGER.exception("Unexpected error in shell: %s", e)
            raise
        finally:
            # Clean up resources
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await orchestrator.stop()
            _LOGGER.debug("Orchestrator stopped")
