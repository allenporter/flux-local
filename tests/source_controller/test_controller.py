"""Tests for the source controller."""

import asyncio
import tempfile
from pathlib import Path
from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch, AsyncMock

import pytest
import git
import yaml

from flux_local.manifest import (
    NamedResource,
    BaseManifest,
    OCIRepository,
    GitRepository,
)
from flux_local.store.in_memory import InMemoryStore
from flux_local.store.status import Status
from flux_local.store.store import StoreEvent
from flux_local.source_controller import GitArtifact, OCIArtifact, SourceController


@pytest.fixture(name="git_repo_tmp_dir", scope="module")
def git_repo_tmp_dir_fixture() -> Generator[Path, None, None]:
    """Create a temporary directory for test resources."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture(name="git_repo_dir", scope="module")
def git_repo_dir_fixture(git_repo_tmp_dir: Path) -> Path:
    """Create a local git repository for testing."""
    repo_path = git_repo_tmp_dir / "test-repo"
    repo_path.mkdir()

    # Initialize git repository
    repo = git.Repo.init(repo_path)
    repo.config_writer().set_value("user", "name", "myusername").release()
    repo.config_writer().set_value("user", "email", "myemail").release()

    # Create a test file
    test_file = repo_path / "test.txt"
    test_file.write_text("Test file content")

    # Add and commit the file
    repo.git.add(".")
    repo.git.commit(m="Initial commit")

    # Create a tag
    repo.git.tag("v1.0.0", m="Initial release")

    return repo_path


@pytest.fixture(name="git_repo")
def git_repo_fixture(git_repo_dir: Path) -> GitRepository:
    """Create a GitRepository object pointing to our local test repo."""
    yaml_str = f"""
    apiVersion: source.toolkit.fluxcd.io/v1
    kind: GitRepository
    metadata:
      name: test-repo
      namespace: test-ns
    spec:
      url: file://{git_repo_dir}
      ref:
        tag: v1.0.0
      interval: 1m0s
    """
    return GitRepository.parse_doc(yaml.safe_load(yaml_str))


@pytest.fixture(name="oci_repo")
def oci_repo_fixture() -> OCIRepository:
    """Create a OCIRepository object for testing."""
    yaml_str = """
    apiVersion: source.toolkit.fluxcd.io/v1
    kind: OCIRepository
    metadata:
      name: test-oci-repo
      namespace: test-ns
    spec:
      url: oci://example.com/repo
      digest: sha256:dummy
      interval: 1m0s
    """
    return OCIRepository.parse_doc(yaml.safe_load(yaml_str))


@pytest.fixture(name="store")
def store_fixture() -> InMemoryStore:
    """Create a test store."""
    return InMemoryStore()


@pytest.fixture(name="controller")
async def controller_fixture(
    store: InMemoryStore,
) -> AsyncGenerator[SourceController, None]:
    """Create a test controller."""
    controller = SourceController(store)
    yield controller
    await controller.close()


@pytest.mark.asyncio
async def test_git_repository_reconciliation(
    git_repo: GitRepository, store: InMemoryStore, controller: SourceController
) -> None:
    """Test Git repository reconciliation."""
    rid = NamedResource(git_repo.kind, git_repo.namespace, git_repo.name)

    # Create an event to wait for reconciliation completion
    reconciliation_complete = asyncio.Event()

    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == rid:
            status = store.get_status(resource_id)
            if status and status.status == Status.READY:
                reconciliation_complete.set()

    # Add our status update listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Add the object to trigger reconciliation
        store.add_object(git_repo)

        # Wait for reconciliation to complete with a timeout
        try:
            await asyncio.wait_for(reconciliation_complete.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            status = store.get_status(rid)
            error_msg = f"Timed out waiting for reconciliation. Current status: {status.status if status else 'unknown'}"
            raise AssertionError(error_msg)

        # Verify the results
        artifact = store.get_artifact(rid, GitArtifact)
        status = store.get_status(rid)
        assert artifact is not None
        assert artifact.url == git_repo.url
        assert artifact.ref
        assert artifact.ref.ref_str == "tag:v1.0.0"
        assert status is not None
        assert status.status == Status.READY

    finally:
        # Clean up
        remove_listener()


@pytest.mark.asyncio
async def test_oci_repository_reconciliation(
    oci_repo: OCIRepository, store: InMemoryStore, controller: SourceController
) -> None:
    """Test OCI repository reconciliation."""
    rid = NamedResource(oci_repo.kind, oci_repo.namespace, oci_repo.name)

    # Create an event to wait for reconciliation completion
    reconciliation_complete = asyncio.Event()

    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == rid:
            status = store.get_status(resource_id)
            if status and status.status == Status.READY:
                reconciliation_complete.set()

    # Add our status update listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Add the object to trigger reconciliation
        with patch("flux_local.source_controller.oci.OrasClient") as mock_client:
            mock_pull = AsyncMock()
            mock_pull.return_value = []  # Resources
            mock_client.return_value.pull = mock_pull
            store.add_object(oci_repo)

            # Wait for reconciliation to complete with a timeout
            try:
                await asyncio.wait_for(reconciliation_complete.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                status = store.get_status(rid)
                error_msg = f"Timed out waiting for reconciliation. Current status: {status.status if status else 'unknown'}"
                raise AssertionError(error_msg)

        # Verify the results
        artifact = store.get_artifact(rid, OCIArtifact)
        status = store.get_status(rid)
        assert artifact is not None
        assert artifact.url == oci_repo.url
        assert status is not None
        assert status.status == Status.READY

    finally:
        remove_listener()


@pytest.mark.asyncio
async def test_git_repository_branch_reconciliation(
    git_repo_dir: Path, store: InMemoryStore, controller: SourceController
) -> None:
    """Test Git repository reconciliation with branch reference."""
    # Create a GitRepository with branch reference
    yaml_str = f"""
    apiVersion: source.toolkit.fluxcd.io/v1
    kind: GitRepository
    metadata:
      name: test-repo
      namespace: test-ns
    spec:
      url: file://{git_repo_dir}
      ref:
        branch: master
      interval: 1m0s
    """
    git_repo = GitRepository.parse_doc(yaml.safe_load(yaml_str))

    rid = NamedResource(git_repo.kind, git_repo.namespace, git_repo.name)

    # Create an event to wait for reconciliation completion
    reconciliation_complete = asyncio.Event()

    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == rid:
            status = store.get_status(resource_id)
            if status and status.status == Status.READY:
                reconciliation_complete.set()

    # Add our status update listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Add the object to trigger reconciliation
        store.add_object(git_repo)

        # Wait for reconciliation to complete with a timeout
        try:
            await asyncio.wait_for(reconciliation_complete.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            status = store.get_status(rid)
            error_msg = f"Timed out waiting for reconciliation. Current status: {status.status if status else 'unknown'}"
            raise AssertionError(error_msg)

        # Verify the results
        artifact = store.get_artifact(rid, GitArtifact)
        status = store.get_status(rid)

    finally:
        remove_listener()

    assert artifact is not None
    assert artifact.url == git_repo.url
    assert artifact.ref is not None
    assert artifact.ref.ref_str == "branch:master"
    assert status is not None
    assert status.status == Status.READY


async def test_unsupported_kind() -> None:
    """Test unsupported kind."""
    store = InMemoryStore()

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
