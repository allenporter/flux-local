"""Test fixtures for the kustomize controller."""

from typing import Any, AsyncGenerator

import pytest
from flux_local.manifest import Kustomization, GitRepository, OCIRepository
from flux_local.store.in_memory import InMemoryStore
from flux_local.kustomize_controller.controller import KustomizationController


@pytest.fixture
def store() -> InMemoryStore:
    """Create an in-memory store for testing."""
    return InMemoryStore()


@pytest.fixture
async def controller(
    store: InMemoryStore,
) -> AsyncGenerator[KustomizationController, None]:
    """Create a KustomizationController with an in-memory store."""
    controller = KustomizationController(store)
    yield controller
    await controller.close()


class DummyKustomization(Kustomization):
    """Test implementation of a Kustomization."""

    kind = "Kustomization"

    def __init__(self, namespace: str, name: str, **kwargs: Any) -> None:
        self.namespace = namespace
        self.name = name
        self.path = kwargs.get("path", "./")
        self.source_kind = kwargs.get("source_kind")
        self.source_name = kwargs.get("source_name")
        self.source_namespace = kwargs.get("source_namespace")
        self.target_namespace = kwargs.get("target_namespace")
        self.depends_on = kwargs.get("depends_on", [])
        self.contents = kwargs.get("contents", {})

    @property
    def namespaced_name(self) -> str:
        return f"{self.namespace}/{self.name}"


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
