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
from typing import Any

import yaml

from flux_local.store import Store
from flux_local.manifest import (
    parse_raw_obj,
    Kustomization,
    FLUXTOMIZE_DOMAIN,
)
from flux_local.exceptions import FluxException

__all__ = ["ResourceLoader", "LoadOptions"]

_LOGGER = logging.getLogger(__name__)

# Type for YAML documents
document = dict[str, Any]


@dataclass
class LoadOptions:
    """Options for configuring resource loading during bootstrap.

    These options control the initial loading of resources from the filesystem
    into the system's store. They are only used during the bootstrap phase.

    Attributes:
        path: Filesystem path to load resources from. Can be a file or directory.
        recursive: If True and path is a directory, load resources from all
                  subdirectories as well.
        include_crds: If False, CustomResourceDefinition resources will be filtered out.
        include_secrets: If False, Secret resources will be filtered out.
    """

    path: Path
    recursive: bool = True
    include_crds: bool = True
    include_secrets: bool = True

    def __post_init__(self) -> None:
        """Resolve the path after initialization."""
        self.path = Path(self.path).expanduser().resolve()


class ResourceLoader:
    """Loads resources from the filesystem into the store."""

    def __init__(self, store: Store) -> None:
        """Initialize the resource loader.

        Args:
            store: The store to load resources into.
        """
        self.store = store
        self._processed_files: set[Path] = set()

    async def load(self, options: LoadOptions) -> None:
        """Load resources from the given options.

        Args:
            options: Options for loading resources.
        """
        _LOGGER.info("Loading resources from %s", options.path)

        if not options.path.exists():
            raise FluxException(f"Path does not exist: {options.path}")

        if options.path.is_file():
            await self._load_file(options.path, options)
        elif options.path.is_dir():
            await self._load_directory(options.path, options)
        else:
            raise FluxException(f"Path is not a file or directory: {options.path}")

        _LOGGER.info("Finished loading resources")

    async def _load_directory(self, path: Path, options: LoadOptions) -> None:
        """Load resources from a directory.

        Args:
            path: Path to the directory to load from.
            options: Load options.
        """
        _LOGGER.debug("Loading directory: %s", path)

        # Process files first, then recurse into subdirectories if enabled
        for entry in sorted(path.iterdir()):
            if entry.is_file() and entry.suffix.lower() in (".yaml", ".yml", ".json"):
                await self._load_file(entry, options)

        if options.recursive:
            for entry in sorted(path.iterdir()):
                if entry.is_dir():
                    await self._load_directory(entry, options)

    async def _load_file(self, path: Path, options: LoadOptions) -> None:
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

        _LOGGER.debug("Loading file: %s", path)
        self._processed_files.add(path)

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            _LOGGER.warning("Failed to read file %s: %s", path, e)
            return

        try:
            for doc in yaml.safe_load_all(content):
                if not doc:
                    continue
                await self._process_document(doc, path, options)
        except yaml.YAMLError as e:
            _LOGGER.warning("Invalid YAML in file %s: %s", path, e)
        except Exception as e:
            _LOGGER.error(
                "Error processing documents from file %s: %s", path, e, exc_info=True
            )

    async def _process_document(
        self, doc: document, path: Path, options: LoadOptions
    ) -> None:
        """Process a single YAML document.

        Args:
            doc: The YAML document to process.
            path: Path to the file containing the document.
            options: Load options.
        """
        _LOGGER.info("Processing document from %s", path)
        if doc.get("api_version", "").startswith(FLUXTOMIZE_DOMAIN):
            return
        try:
            resource = parse_raw_obj(doc)
            if isinstance(resource, Kustomization):
                _LOGGER.info("Bootstrapping resource %s from %s", resource.name, path)
                self.store.add_object(resource)
        except FluxException as e:
            _LOGGER.info("Skipping document in %s: %s", path, e)
        except Exception as e:
            _LOGGER.error(
                "Failed to process document from %s: %s", path, e, exc_info=True
            )
