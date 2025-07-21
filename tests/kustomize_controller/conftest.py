"""Test fixtures for the kustomize controller."""

from typing import AsyncGenerator

import pytest
from flux_local.manifest import GitRepository, OCIRepository
from flux_local.store.in_memory import InMemoryStore
from flux_local.kustomize_controller.controller import (
    KustomizationController,
    KustomizationControllerConfig,
)


@pytest.fixture
def store() -> InMemoryStore:
    """Create an in-memory store for testing."""
    return InMemoryStore()


@pytest.fixture
async def controller(
    store: InMemoryStore,
) -> AsyncGenerator[KustomizationController, None]:
    """Create a KustomizationController with an in-memory store."""
    controller = KustomizationController(store, KustomizationControllerConfig())
    yield controller
    await controller.close()


class DummyGitRepository(GitRepository):
    """Test implementation of a GitRepository source."""

    kind = "GitRepository"

    def __init__(self, namespace: str, name: str) -> None:
        self.namespace = namespace
        self.name = name
        self.url = f"https://example.com/{namespace}/{name}.git"
        self.revision = "main"


class DummyOCIRepository(OCIRepository):
    """Test implementation of an OCIRepository source."""

    kind = "OCIRepository"

    def __init__(self, namespace: str, name: str) -> None:
        self.namespace = namespace
        self.name = name
        self.url = f"oci://example.com/{namespace}/{name}"
        self.digest = f"sha256:{name}"
