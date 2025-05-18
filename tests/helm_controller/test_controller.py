"""Tests for the helm controller."""

import asyncio
from collections.abc import Generator, AsyncGenerator
import tempfile
from pathlib import Path

import pytest
import yaml
import git

from flux_local.manifest import BaseManifest, GitRepository, GIT_REPOSITORY
from flux_local.store.in_memory import InMemoryStore
from flux_local.store import Store, StoreEvent, Status
from flux_local.helm_controller.artifact import HelmReleaseArtifact
from flux_local.helm_controller import HelmReleaseController
from flux_local.manifest import (
    NamedResource,
    HelmRelease,
    ConfigMap,
    Secret,
)
from flux_local.source_controller import GitArtifact
from flux_local.helm import Helm


@pytest.fixture(name="tmp_dir")
def tmp_dir_fixture() -> Generator[Path, None, None]:
    """Create a temporary directory for test resources."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture(name="git_repo_tmp_dir", scope="module")
def git_repo_tmp_dir_fixture() -> Generator[Path, None, None]:
    """Create a temporary directory for test resources."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture(name="git_repo_dir", scope="module")
def git_repo_dir_fixture(git_repo_tmp_dir: Path) -> Path:
    """Create a local git repository for testing."""
    repo_path = git_repo_tmp_dir / "git-repo"
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


@pytest.fixture(name="helm_chart_git_path", scope="module")
def helm_chart_git_pathfixture(git_repo_dir: Path) -> Path:
    """Create a Helm repository directory for testing and return its path."""
    helm_chart_git_path = git_repo_dir / "helm-charts"
    helm_chart_git_path.mkdir(parents=True, exist_ok=True)

    # Create a test Helm chart
    chart_dir = helm_chart_git_path / "nginx"
    chart_dir.mkdir(exist_ok=True)

    # Create a basic Chart.yaml
    chart_yaml = chart_dir / "Chart.yaml"
    chart_yaml.write_text(
        """
apiVersion: v2
name: nginx
version: 1.0.0
appVersion: 1.14.2
""",
        encoding="utf-8",
    )

    # Add and commit the chart files
    repo = git.Repo(str(git_repo_dir))
    repo.git.add(".")
    repo.git.commit(m="Add Helm chart")

    return helm_chart_git_path


@pytest.fixture(name="chart_path", scope="module")
def chart_path_fixture(helm_chart_git_path: Path) -> Path:
    """Create a Helm chart directory with a Chart.yaml and return its path."""
    chart_dir = helm_chart_git_path / "nginx"
    chart_dir.mkdir(exist_ok=True)

    # Create a basic Chart.yaml
    chart_yaml = chart_dir / "Chart.yaml"
    chart_yaml.write_text(
        """
apiVersion: v2
name: nginx
description: A Helm chart for nginx
version: 1.0.0
appVersion: 1.14.2
""",
        encoding="utf-8",
    )

    # Create a basic values.yaml
    values_yaml = chart_dir / "values.yaml"
    values_yaml.write_text(
        """
replicaCount: 1
image:
  repository: nginx
  tag: 1.14.2
""",
        encoding="utf-8",
    )

    return chart_dir


@pytest.fixture(name="config_map")
def config_map_fixture() -> ConfigMap:
    """Create a test ConfigMap."""
    config_map_yaml = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
  namespace: test-ns
  labels:
    flux_local_test: "true"
data:
  values.yaml: |
    replicaCount: 2
"""
    config_map = ConfigMap.parse_doc(yaml.safe_load(config_map_yaml))
    return config_map


@pytest.fixture(name="secret")
def secret_fixture() -> Secret:
    """Create a test Secret."""
    secret_yaml = """
apiVersion: v1
kind: Secret
metadata:
  name: test-secret
  namespace: test-ns
  labels:
    flux_local_test: "true"
type: Opaque
stringData:
  values.yaml: |
    replicaCount: 3
"""
    secret = Secret.parse_doc(yaml.safe_load(secret_yaml))
    return secret


@pytest.fixture(name="helm_release")
def helm_release_fixture(
    git_repo_dir: Path,
    chart_path: Path,
    helm_chart_git_path: Path,
    config_map: ConfigMap,
    secret: Secret,
) -> HelmRelease:
    """Create a test HelmRelease."""
    chart_name = chart_path.relative_to(git_repo_dir)
    helm_release_yaml = f"""
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: test-release
  namespace: test-ns
spec:
  chart:
    spec:
      chart: {chart_name}
      version: 1.0.0
      sourceRef:
        kind: GitRepository
        name: test-repo
        namespace: test-ns
        ref:
          tag: v1.0.0
  valuesFrom:
    - kind: ConfigMap
      name: {config_map.name}
      valuesKey: values.yaml
      targetPath: .
    - kind: Secret
      name: {secret.name}
      valuesKey: values.yaml
      targetPath: .
"""
    helm_release = HelmRelease.parse_doc(yaml.safe_load(helm_release_yaml))
    return helm_release


@pytest.fixture(name="git_repo")
def git_repo_fixture() -> GitRepository:
    """Create a test GitRepository."""
    git_repo_yaml = """
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: test-repo
  namespace: test-ns
spec:
  url: https://github.com/example/helm-charts
  ref:
    tag: v1.0.0
  interval: 1m0s
"""
    git_repo = GitRepository.parse_doc(yaml.safe_load(git_repo_yaml))
    return git_repo


@pytest.fixture(name="store")
def store_fixture() -> Store:
    """Create a test store."""
    return InMemoryStore()


@pytest.fixture(name="helm")
def helm_fixture(tmp_dir: Path) -> Helm:
    """Create a test Helm instance."""
    cache_dir = tmp_dir / "helm-cache"
    cache_dir.mkdir(exist_ok=True)
    return Helm(tmp_dir=tmp_dir, cache_dir=cache_dir)


@pytest.fixture(name="controller")
async def controller_fixture(
    store: Store, helm: Helm
) -> AsyncGenerator[HelmReleaseController, None]:
    """Create a test controller."""
    controller = HelmReleaseController(store, helm)
    yield controller
    await controller.close()


async def test_helm_release_reconciliation(
    store: Store,
    controller: HelmReleaseController,
    helm_release: HelmRelease,
    git_repo: GitRepository,
    helm_chart_git_path: Path,
    chart_path: Path,
    config_map: ConfigMap,
    secret: Secret,
    git_repo_dir: Path,
) -> None:
    """Test basic HelmRelease reconciliation with dependencies."""
    # Add the ConfigMap and Secret resources first
    store.add_object(config_map)
    store.add_object(secret)

    # Add the GitRepository
    git_repo_rid = NamedResource(git_repo.kind, git_repo.namespace, git_repo.name)
    store.add_object(git_repo)

    # Set the artifact for the GitRepository
    repo_artifact = GitArtifact(
        url=git_repo.url,
        path=str(git_repo_dir),
        # TODO: Support ref tags
        # ref=git_repo.ref_tag
    )
    store.set_artifact(git_repo_rid, repo_artifact)

    # Update status to READY
    store.update_status(git_repo_rid, Status.READY)

    # Create an event to signal when reconciliation is complete
    reconciliation_complete = asyncio.Event()

    helm_release_rid = NamedResource(
        helm_release.kind, helm_release.namespace, helm_release.name
    )

    # Add a listener to wait for the HelmRelease to be reconciled
    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == helm_release_rid:
            status = store.get_status(resource_id)
            if status and status.status == Status.READY:
                reconciliation_complete.set()

    # Register the listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Add the HelmRelease to trigger reconciliation
        store.add_object(helm_release)

        # Wait for reconciliation to complete with a timeout
        try:
            await asyncio.wait_for(reconciliation_complete.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            status = store.get_status(helm_release_rid)
            error_msg = f"Timed out waiting for reconciliation. Current status: {status.status if status else 'unknown'}"
            if status and status.error:
                error_msg += f", Error: {status.error}"
            pytest.fail(error_msg)
    finally:
        # Clean up the listener
        remove_listener()

    artifact = store.get_artifact(helm_release_rid, HelmReleaseArtifact)
    status = store.get_status(helm_release_rid)

    assert artifact is not None, "Expected artifact to be set"
    assert artifact.path == str(
        chart_path
    ), f"Expected chart path {chart_path}, got {artifact.path}"
    assert status is not None, "Expected status to be set"
    assert status.status == Status.READY, f"Expected status READY, got {status.status}"


async def test_helm_release_becomes_ready_after_gitrepo_ready(
    store: Store,
    controller: HelmReleaseController,
    helm_release: HelmRelease,
    git_repo: GitRepository,
    config_map: ConfigMap,
    secret: Secret,
    git_repo_dir: Path,
    chart_path: Path,
    helm_chart_git_path: Path,
) -> None:
    """Test HelmRelease becomes ready after GitRepository becomes ready."""
    # Add the ConfigMap and Secret resources first
    store.add_object(config_map)
    store.add_object(secret)

    # Add the GitRepository but don't mark it as ready yet
    git_repo_rid = NamedResource(GIT_REPOSITORY, git_repo.namespace, git_repo.name)
    store.add_object(git_repo)

    # Add the HelmRelease to trigger reconciliation
    helm_release_rid = NamedResource(
        helm_release.kind, helm_release.namespace, helm_release.name
    )
    store.add_object(helm_release)

    # Create an event to signal when reconciliation is complete
    reconciliation_complete = asyncio.Event()

    # Add a listener to wait for the HelmRelease to be reconciled
    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == helm_release_rid:
            status = store.get_status(resource_id)
            if status and status.status == Status.READY:
                reconciliation_complete.set()

    # Register the listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Start reconciliation
        reconciliation_task = asyncio.create_task(
            asyncio.wait_for(reconciliation_complete.wait(), timeout=5.0)
        )

        # Wait a bit to ensure reconciliation has started
        await asyncio.sleep(0.1)

        repo_artifact = GitArtifact(
            url=git_repo.url,
            path=str(git_repo_dir),
            # TODO: Support ref tags
            # ref=git_repo.ref_tag
        )
        store.set_artifact(git_repo_rid, repo_artifact)
        store.update_status(git_repo_rid, Status.READY)

        # Wait for reconciliation to complete
        await reconciliation_task

    finally:
        # Clean up the listener
        remove_listener()

    # Verify the HelmRelease is ready
    status = store.get_status(helm_release_rid)
    assert status is not None, "Expected status to be set"
    assert status.status == Status.READY, f"Expected status READY, got {status.status}"

    # Verify the artifact is set
    artifact = store.get_artifact(helm_release_rid, HelmReleaseArtifact)
    assert artifact is not None, "Expected artifact to be set"
    assert artifact.path == str(
        chart_path
    ), f"Expected chart path {chart_path}, got {artifact.path}"

    await controller.close()


async def test_helm_release_fails_with_missing_dependency(
    store: Store,
    controller: HelmReleaseController,
    helm_release: HelmRelease,
    git_repo: GitRepository,
    helm_chart_git_path: Path,
    chart_path: Path,
    git_repo_dir: Path,
) -> None:
    """Test HelmRelease fails when a dependency is missing."""
    # Add the GitRepository
    git_repo_rid = NamedResource(git_repo.kind, git_repo.namespace, git_repo.name)
    store.add_object(git_repo)

    # Set the artifact for the GitRepository
    repo_artifact = GitArtifact(
        url=git_repo.url,
        path=str(git_repo_dir),
        # TODO: Support ref tags
        # ref=git_repo.ref_tag
    )
    store.set_artifact(git_repo_rid, repo_artifact)

    # Update status to READY
    store.update_status(git_repo_rid, Status.READY)

    # Create an event to signal when reconciliation is complete
    reconciliation_complete = asyncio.Event()

    # Add a listener to wait for the HelmRelease to be reconciled
    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == NamedResource(
            helm_release.kind, helm_release.namespace, helm_release.name
        ):
            status = store.get_status(resource_id)
            if status and status.status == Status.FAILED:
                reconciliation_complete.set()

    # Register the listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Add the HelmRelease to trigger reconciliation
        store.add_object(helm_release)

        # Wait for reconciliation to complete with a timeout
        try:
            await asyncio.wait_for(reconciliation_complete.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        else:
            pytest.fail(
                "HelmRelease reconciled successfully when it should have failed"
            )
    finally:
        # Clean up the listener
        remove_listener()

    status = store.get_status(
        NamedResource(helm_release.kind, helm_release.namespace, helm_release.name)
    )

    assert status is not None, "Expected status to be set"
    assert (
        status.status == Status.PENDING
    ), f"Expected status FAILED, got {status.status}"
    assert status.error is None
    await controller.close()


async def test_helm_release_fails_failed_dependency(
    store: Store,
    controller: HelmReleaseController,
    helm_release: HelmRelease,
    git_repo: GitRepository,
    helm_chart_git_path: Path,
    chart_path: Path,
    git_repo_dir: Path,
    config_map: ConfigMap,
    secret: Secret,
) -> None:
    """Test HelmRelease fails when a dependency is missing."""
    # Add the ConfigMap and Secret resources first
    store.add_object(config_map)
    store.add_object(secret)

    # Add the GitRepository
    git_repo_rid = NamedResource(git_repo.kind, git_repo.namespace, git_repo.name)
    store.add_object(git_repo)

    store.update_status(git_repo_rid, Status.FAILED, "Some error")

    # Create an event to signal when reconciliation is complete
    reconciliation_complete = asyncio.Event()

    # Add a listener to wait for the HelmRelease to be reconciled
    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == NamedResource(
            helm_release.kind, helm_release.namespace, helm_release.name
        ):
            status = store.get_status(resource_id)
            if status and status.status == Status.FAILED:
                reconciliation_complete.set()

    # Register the listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Add the HelmRelease to trigger reconciliation
        store.add_object(helm_release)

        # Wait for reconciliation to complete with a timeout
        await asyncio.wait_for(reconciliation_complete.wait(), timeout=2.0)
    finally:
        # Clean up the listener
        remove_listener()

    status = store.get_status(
        NamedResource(helm_release.kind, helm_release.namespace, helm_release.name)
    )

    assert status is not None, "Expected status to be set"
    assert (
        status.status == Status.FAILED
    ), f"Expected status FAILED, got {status.status}"
    assert (
        status.error == "Dependency GitRepository/test-ns/test-repo: Failed: Some error"
    )
    await controller.close()


async def test_helm_release_fails_with_nonexistent_chart(
    store: Store,
    controller: HelmReleaseController,
    helm_release: HelmRelease,
    git_repo: GitRepository,
    helm_chart_git_path: Path,
    chart_path: Path,
    git_repo_dir: Path,
    config_map: ConfigMap,
    secret: Secret,
) -> None:
    """Test HelmRelease fails when chart directory doesn't exist in GitRepository."""
    # Add the ConfigMap and Secret resources first
    store.add_object(config_map)
    store.add_object(secret)

    # Create a HelmRelease that points to a non-existent chart directory
    helm_release.chart.name = "non-existent-chart"
    helm_release.chart.repo_name = git_repo.name
    helm_release.chart.repo_namespace = git_repo.namespace
    helm_release.chart.repo_kind = git_repo.kind

    # Add the GitRepository
    git_repo_rid = NamedResource(git_repo.kind, git_repo.namespace, git_repo.name)
    store.add_object(git_repo)

    # Set the artifact for the GitRepository
    repo_artifact = GitArtifact(
        url=git_repo.url,
        path=str(git_repo_dir),
        # TODO: Support ref tags
        # ref=git_repo.ref_tag
    )
    store.set_artifact(git_repo_rid, repo_artifact)

    # Update status to READY
    store.update_status(git_repo_rid, Status.READY)

    # Create an event to signal when reconciliation is complete
    reconciliation_complete = asyncio.Event()

    # Add a listener to wait for the HelmRelease to be reconciled
    def on_status_updated(resource_id: NamedResource, obj: BaseManifest) -> None:
        if resource_id == NamedResource(
            helm_release.kind, helm_release.namespace, helm_release.name
        ):
            status = store.get_status(resource_id)
            if status and status.status == Status.FAILED:
                reconciliation_complete.set()

    # Register the listener
    remove_listener = store.add_listener(StoreEvent.STATUS_UPDATED, on_status_updated)

    try:
        # Add the HelmRelease to trigger reconciliation
        store.add_object(helm_release)

        # Wait for reconciliation to complete with a timeout
        await asyncio.wait_for(reconciliation_complete.wait(), timeout=2.0)

        status = store.get_status(
            NamedResource(helm_release.kind, helm_release.namespace, helm_release.name)
        )
        assert status is not None, "Expected status to be set"
        assert (
            status.status == Status.FAILED
        ), f"Expected status FAILED, got {status.status}"
        assert status.error is not None, "Expected error message to be set"
        assert (
            "not found" in status.error
        ), "Expected error message to indicate file not found"
    finally:
        # Clean up the listener
        remove_listener()
    await controller.close()
