"""Common utilities for get commands."""

import logging
import pathlib
from argparse import ArgumentParser, BooleanOptionalAction

from flux_local.orchestrator import Orchestrator, OrchestratorConfig, BootstrapOptions
from flux_local.store import InMemoryStore
from flux_local.exceptions import FluxException

_LOGGER = logging.getLogger(__name__)


def add_common_flags(args: ArgumentParser) -> None:
    """Add common flags to the arguments object."""
    args.add_argument(
        "--enable-oci",
        default=False,
        action=BooleanOptionalAction,
        help="Enable OCI repository sources",
    )


async def bootstrap(path: pathlib.Path, enable_oci: bool) -> InMemoryStore:
    """Bootstrap the orchestrator and return the store."""
    # We don't need to enable helm controller to just list HelmReleases
    # found in Kustomizations.
    config = OrchestratorConfig(enable_helm=False)
    config.source_controller_config.enable_oci = enable_oci

    store = InMemoryStore()
    orchestrator = Orchestrator(store, config)
    bootstrap_options = BootstrapOptions(path=path)

    try:
        await orchestrator.bootstrap(bootstrap_options)
    except FluxException:
        # Continue to show what we found, even if some failed
        pass

    return store
