"""Tests for the source controller."""

import asyncio
import pytest
from flux_local.manifest import NamedResource, BaseManifest, OCIRepository
from flux_local.store.in_memory import InMemoryStore
from flux_local.store.status import Status
from flux_local.source_controller import GitArtifact, OCIArtifact, SourceController
from flux_local.source_controller.git import GitRepository


class DummyGitRepository(GitRepository):
    kind = "GitRepository"

    def __init__(self, namespace: str, name: str) -> None:
        self.namespace = namespace
        self.name = name
        self.url = "https://example.com/repo.git"
        self.revision = "dummyrev"


class DummyOCIRepository(OCIRepository):
    kind = "OCIRepository"

    def __init__(self, namespace: str, name: str) -> None:
        self.namespace = namespace
        self.name = name
        self.url = "oci://example.com/repo"
        self.digest = "sha256:dummy"


@pytest.mark.asyncio
async def test_git_repository_reconciliation() -> None:
    """Test Git repository reconciliation."""
    store = InMemoryStore()
    controller = SourceController(store)
    obj = DummyGitRepository(namespace="ns", name="foo")
    rid = NamedResource(obj.kind, obj.namespace, obj.name)
    store.add_object(obj)
    # Wait for async reconciliation
    await asyncio.sleep(0.1)
    artifact = store.get_artifact(rid, GitArtifact)
    status = store.get_status(rid)
    assert artifact is not None
    assert artifact.url == obj.url
    assert status is not None
    assert status.status == Status.READY
    await controller.close()


@pytest.mark.asyncio
async def test_oci_repository_reconciliation() -> None:
    """Test OCI repository reconciliation."""
    store = InMemoryStore()
    controller = SourceController(store)
    obj = DummyOCIRepository(namespace="ns", name="bar")
    rid = NamedResource(obj.kind, obj.namespace, obj.name)
    store.add_object(obj)
    await asyncio.sleep(0.1)
    artifact = store.get_artifact(rid, OCIArtifact)
    status = store.get_status(rid)
    assert artifact is not None
    assert artifact.url == obj.url
    assert status is not None
    assert status.status == Status.READY
    await controller.close()


@pytest.mark.asyncio
async def test_unsupported_kind() -> None:
    """Test unsupported kind."""
    store = InMemoryStore()
    controller = SourceController(store)

    class DummyOther(BaseManifest):
        kind = "OtherKind"

        def __init__(self) -> None:
            self.namespace = "ns"
            self.name = "baz"

    obj = DummyOther()
    rid = NamedResource(obj.kind, obj.namespace, obj.name)
    store.add_object(obj)
    await asyncio.sleep(0.1)
    assert store.get_artifact(rid, GitArtifact) is None
    assert store.get_status(rid) is None
    await controller.close()
