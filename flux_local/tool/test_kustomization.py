"""Flux local test action."""

import asyncio
import logging
from pathlib import Path
from typing import Generator

import nest_asyncio
import pytest

from flux_local import kustomize
from flux_local.manifest import Manifest

_LOGGER = logging.getLogger(__name__)


class KustomizationTest(pytest.Item):
    """Test case for a Kustomization."""

    def runtest(self) -> None:
        """Dispatch the async work and run the test."""
        nest_asyncio.apply()
        asyncio.run(self.async_runtest())

    async def async_runtest(self) -> None:
        """Run the Kustomizations test."""
        await kustomize.build(self.path).objects()


class ManifestCollector(pytest.File):
    """Test collector that generates tests to run from the cluster manifest."""

    def collect(self) -> Generator[pytest.Item, None, None]:
        """Collect tests from the cluster manifest.yaml file."""
        manifest = Manifest.parse_yaml(self.path.read_text(encoding="utf-8"))
        for cluster in manifest.clusters:
            for kustomization in cluster.kustomizations:
                yield KustomizationTest.from_parent(
                    self,
                    name=kustomization.name,
                    path=Path(kustomization.path),
                )


def pytest_collect_file(parent: pytest.Session, file_path: Path) -> pytest.Collector:
    """Build the test of test cases from the cluster manifest."""
    _LOGGER.debug("pytest_collect_file: %s, %s", parent, file_path)
    return ManifestCollector.from_parent(  # type: ignore[no-any-return]
        parent, path=file_path
    )
