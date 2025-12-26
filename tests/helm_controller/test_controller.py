"""Tests for the helm controller."""

import asyncio
from collections.abc import Generator, AsyncGenerator
import tempfile
from pathlib import Path

import pytest
import yaml
import git
from syrupy.assertion import SnapshotAssertion

from flux_local.manifest import GitRepository, GIT_REPOSITORY
from flux_local.store.in_memory import InMemoryStore
from flux_local.store import Store, Status
from flux_local.helm_controller.artifact import HelmReleaseArtifact
from flux_local.helm_controller import HelmReleaseController, HelmControllerConfig
from flux_local.manifest import (
    NamedResource,
    HelmRelease,
    HelmChartSource,
    ConfigMap,
    Secret,
)
from flux_local.source_controller import GitArtifact
from flux_local.helm import Helm
from flux_local.task import get_task_service, TaskService, task_service_context


@pytest.fixture(name="task_service", autouse=True)
def task_service_fixture() -> Generator[TaskService, None, None]:
    """Create a task service for testing."""
    with task_service_context() as service:
        yield service


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
replicaCount: 7
""",
        encoding="utf-8",
    )
    templates = chart_dir / "templates"
    templates.mkdir(exist_ok=True)

    config_map_yaml = templates / "configmap.yaml"
    config_map_yaml.write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-configmap
data:
  myvalue: "Hello World with {{ .Values.replicaCount }} replicas"
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
    controller = HelmReleaseController(store, helm, HelmControllerConfig())
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
    snapshot: SnapshotAssertion,
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
        local_path=str(git_repo_dir),
        ref=git_repo.ref,
    )
    store.set_artifact(git_repo_rid, repo_artifact)

    # Update status to READY
    store.update_status(git_repo_rid, Status.READY)

    helm_release_rid = NamedResource(
        helm_release.kind, helm_release.namespace, helm_release.name
    )

    # Add the HelmRelease to trigger reconciliation
    store.add_object(helm_release)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    status = store.get_status(helm_release_rid)
    assert status is not None
    assert status.status == Status.READY

    artifact = store.get_artifact(helm_release_rid, HelmReleaseArtifact)
    assert artifact is not None
    assert artifact.chart_name == helm_release.chart.chart_name

    # Verify objects are applied
    objects = store.list_objects()
    assert [
        obj
        for obj in objects
        # GitRepository has a tmpdir random path so ignore
        if hasattr(obj, "kind") and obj.kind != "GitRepository"
    ] == snapshot


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

    # Wait for reconciliation to start
    await asyncio.sleep(0.001)

    repo_artifact = GitArtifact(
        url=git_repo.url,
        local_path=str(git_repo_dir),
        ref=git_repo.ref,
    )
    store.set_artifact(git_repo_rid, repo_artifact)
    store.update_status(git_repo_rid, Status.READY)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    # Verify the HelmRelease is ready
    status = store.get_status(helm_release_rid)
    assert status is not None
    assert status.status == Status.READY

    # Verify the artifact is set
    artifact = store.get_artifact(helm_release_rid, HelmReleaseArtifact)
    assert artifact is not None
    assert artifact.chart_name == helm_release.chart.chart_name


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
        local_path=str(git_repo_dir),
        ref=git_repo.ref,
    )
    store.set_artifact(git_repo_rid, repo_artifact)

    # Update status to READY
    store.update_status(git_repo_rid, Status.READY)

    # Add the HelmRelease to trigger reconciliation
    store.add_object(helm_release)

    # Wait for reconciliation to complete and fail
    task_service = get_task_service()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    status = store.get_status(
        NamedResource(helm_release.kind, helm_release.namespace, helm_release.name)
    )
    assert status is not None
    assert status.status == Status.FAILED
    # TODO: Make this fail or pending without a timeout
    assert status.error == "Reconciliation failed: TimeoutError: "


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

    # Add the HelmRelease to trigger reconciliation
    store.add_object(helm_release)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    status = store.get_status(
        NamedResource(helm_release.kind, helm_release.namespace, helm_release.name)
    )
    assert status is not None
    assert status.status == Status.FAILED
    assert (
        status.error
        == "Reconciliation failed: HelmControllerException: Dependency GitRepository/test-ns/test-repo: Failed: Some error"
    )


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
        local_path=str(git_repo_dir),
        ref=git_repo.ref,
    )
    store.set_artifact(git_repo_rid, repo_artifact)

    # Update status to READY
    store.update_status(git_repo_rid, Status.READY)

    # Add the HelmRelease to trigger reconciliation
    store.add_object(helm_release)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    status = store.get_status(
        NamedResource(helm_release.kind, helm_release.namespace, helm_release.name)
    )
    assert status is not None
    assert status.status == Status.FAILED
    assert status.error is not None
    assert "not found" in status.error


async def test_helm_release_resolves_chartref(
    store: Store,
    controller: HelmReleaseController,
    git_repo: GitRepository,
    git_repo_dir: Path,
    chart_path: Path,
) -> None:
    """Ensure chartRef HelmReleases resolve HelmChart resources."""
    helm_chart_yaml = f"""
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmChart
metadata:
  name: test-chart
  namespace: test-ns
spec:
  chart: {chart_path.relative_to(git_repo_dir)}
  version: 1.0.0
  sourceRef:
    kind: GitRepository
    name: {git_repo.name}
    namespace: {git_repo.namespace}
"""
    helm_chart = HelmChartSource.parse_doc(yaml.safe_load(helm_chart_yaml))
    store.add_object(helm_chart)

    git_repo_rid = NamedResource(git_repo.kind, git_repo.namespace, git_repo.name)
    store.add_object(git_repo)
    repo_artifact = GitArtifact(
        url=git_repo.url,
        local_path=str(git_repo_dir),
        ref=git_repo.ref,
    )
    store.set_artifact(git_repo_rid, repo_artifact)
    store.update_status(git_repo_rid, Status.READY)

    helm_release_yaml = f"""
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: test-release
  namespace: test-ns
spec:
  chartRef:
    kind: HelmChart
    name: {helm_chart.name}
    namespace: {helm_chart.namespace}
"""
    helm_release = HelmRelease.parse_doc(yaml.safe_load(helm_release_yaml))
    helm_release_rid = NamedResource(
        helm_release.kind, helm_release.namespace, helm_release.name
    )

    store.add_object(helm_release)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    status = store.get_status(helm_release_rid)
    assert status is not None
    assert status.status == Status.READY

    resolved_release = store.get_object(helm_release_rid, HelmRelease)
    assert resolved_release is not None
    assert resolved_release.chart.version == helm_chart.version
    assert resolved_release.chart.repo_kind == git_repo.kind
    assert resolved_release.chart.repo_name == git_repo.name
    assert resolved_release.chart.name == helm_chart.chart
