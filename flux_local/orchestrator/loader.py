"""Resource loader for flux-local bootstrap process.

This module provides the ResourceLoader class which is responsible for the initial
loading of Kubernetes manifests from the filesystem into the system's store during bootstrap.

Key Characteristics:
- Used only during system initialization to populate the initial state
- Handles basic YAML/JSON parsing and validation
- Filters resources based on simple criteria (CRDs, Secrets, etc.)
- Not involved in the reconciliation loop or watching for changes
- Stateless - all state is stored in the provided Store

After bootstrap, controllers take over for:
- Managing source repositories (SourceController)
- Building kustomizations (KustomizeController)
- Handling Helm releases (HelmReleaseController)
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator
import asyncio

import yaml

from flux_local.manifest import (
    parse_raw_obj,
    BaseManifest,
)
from flux_local.exceptions import FluxException, InputException

__all__ = ["ResourceLoader", "LoadOptions"]

_LOGGER = logging.getLogger(__name__)

# Type for YAML documents
document = dict[str, Any]


@dataclass
class ReadAction:
    """Configuration for reading resources."""

    wipe_secrets: bool = True


@dataclass
class LoadOptions:
    """Options for configuring resource loading during bootstrap.

    These options control the initial loading of resources from the filesystem
    into the system's store. They are only used during the bootstrap phase.

    Attributes:
        path: Filesystem path to load resources from. Can be a file or directory.
        recursive: If True and path is a directory, load resources from all
                  subdirectories as well.
    """

    path: Path
    recursive: bool = True

    def __post_init__(self) -> None:
        """Resolve the path after initialization."""
        self.path = Path(self.path).expanduser().resolve()


class ResourceLoader:
    """Loads resources from the filesystem."""

    def __init__(self, config: ReadAction) -> None:
        """Initialize the resource loader."""
        self._config = config
        self._processed_files: set[Path] = set()

    async def load(self, options: LoadOptions) -> AsyncGenerator[BaseManifest, None]:
        """Load resources from the given options.

        Args:
            options: Options for loading resources.
        """
        _LOGGER.info("Loading resources from %s", options.path)

        if not options.path.exists():
            raise FluxException(f"Path does not exist: {options.path}")

        if options.path.is_file():
            async for resource in self._load_file(options.path, options):
                yield resource
        elif options.path.is_dir():
            async for resource in self._load_directory(options.path, options):
                yield resource
        else:
            raise FluxException(f"Path is not a file or directory: {options.path}")

        _LOGGER.info("Finished loading resources")

    async def _load_directory(
        self, path: Path, options: LoadOptions
    ) -> AsyncGenerator[BaseManifest, None]:
        """Load resources from a directory.

        Args:
            path: Path to the directory to load from.
            options: Load options.
        """
        _LOGGER.debug("Loading directory: %s", path)

        # Process files first, then recurse into subdirectories if enabled
        for entry in sorted(path.iterdir()):
            if entry.is_file() and entry.suffix.lower() in (".yaml", ".yml", ".json"):
                async for resource in self._load_file(entry, options):
                    yield resource
            elif options.recursive and entry.is_dir():
                async for resource in self._load_directory(entry, options):
                    yield resource

    async def _load_file(
        self, path: Path, options: LoadOptions
    ) -> AsyncGenerator[BaseManifest, None]:
        """Load resources from a file.

        Args:
            path: Path to the file to load.
            options: Load options.

        Raises:
            FluxException: If there's an error reading the file.
        """
        if path in self._processed_files:
            _LOGGER.debug("Skipping already processed file: %s", path)
            return

        _LOGGER.debug("Processing file: %s", path)
        self._processed_files.add(path)

        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError as e:
            raise FluxException(f"Failed to read file {path}: {e}") from e

        try:
            for doc in yaml.safe_load_all(content):
                if not doc:
                    continue
                try:
                    yield parse_raw_obj(doc, wipe_secrets=self._config.wipe_secrets)
                except InputException as e:
                    _LOGGER.info("Skipping document in %s: %s", path, e)
        except yaml.YAMLError as e:
            raise FluxException(f"Invalid YAML in file {path}: {e}") from e
        except Exception as e:
            raise FluxException(f"Error processing file {path}: {e}") from e
