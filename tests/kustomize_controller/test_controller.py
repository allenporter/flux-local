"""Tests for the kustomize controller."""

import asyncio
from collections.abc import Generator
import tempfile
from pathlib import Path

import pytest
import yaml
from syrupy.assertion import SnapshotAssertion

from flux_local.store import Store, Status
from flux_local.kustomize_controller import (
    KustomizationArtifact,
    KustomizationController,
)
from flux_local.source_controller import GitArtifact, OCIArtifact
from flux_local.manifest import (
    NamedResource,
    Kustomization,
    GitRepository,
    OCIRepository,
)
from flux_local.task import get_task_service, TaskService, task_service_context


@pytest.fixture(name="task_service", autouse=True)
def task_service_fixture() -> Generator[TaskService, None, None]:
    """Create a task service for testing."""
    with task_service_context() as service:
        yield service


@pytest.fixture(name="tmp_dir")
def tmp_dir_fixture() -> Generator[Path, None, None]:
    """Create a temporary directory for test kustomizations."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture(name="git_repo_path")
def git_repo_path_fixture(tmp_dir: Path) -> Path:
    """Create a Git repository directory for testing and return its path."""
    git_repo_path = tmp_dir / "git-repo"
    git_repo_path.mkdir(parents=True, exist_ok=True)
    return git_repo_path


@pytest.fixture(name="fluxtomization_path")
def fluxtomization_path_fixture(git_repo_path: Path) -> Path:
    """Create an app directory with a kustomization file and return its path."""
    cluster_dir = git_repo_path / "cluster"
    cluster_dir.mkdir(exist_ok=True)

    # Create a basic kustomization file
    kustomization_path = cluster_dir / "app.yaml"
    kustomization_path.write_text(
        """
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: app
  namespace: default
spec:
  interval: 10m
  targetNamespace: default
  sourceRef:
    kind: GitRepository
    name: git-repo
    namespace: test-ns
  path: "./app"
""",
        encoding="utf-8",
    )

    return kustomization_path


@pytest.fixture(name="oci_fluxtomization_path")
def oci_fluxtomization_path_fixture(git_repo_path: Path) -> Path:
    """Create an app directory with a kustomization file and return its path."""
    cluster_dir = git_repo_path / "cluster"
    cluster_dir.mkdir(exist_ok=True)

    # Create a basic kustomization file
    kustomization_path = cluster_dir / "app.yaml"
    kustomization_path.write_text(
        """
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: app
  namespace: default
spec:
  interval: 10m
  targetNamespace: default
  sourceRef:
    kind: OCIRepository
    name: test-repo
    namespace: test-ns
  path: "./app"
""",
        encoding="utf-8",
    )

    return kustomization_path


@pytest.fixture(name="app_dir")
def app_dir_fixture(git_repo_path: Path) -> Path:
    """Create an app directory with a kustomization file and return its path."""
    app_dir = git_repo_path / "app"
    app_dir.mkdir(exist_ok=True)

    # Create a basic kustomization file
    kustomization_path = app_dir / "kustomization.yaml"
    kustomization_path.write_text(
        """
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- config_map.yaml
- helm_repo.yaml
- helm_release.yaml
""",
        encoding="utf-8",
    )

    # Create a config map file that the kustomization references
    config_map_path = app_dir / "config_map.yaml"
    config_map_path.write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  key: value
""",
        encoding="utf-8",
    )

    helm_repo_path = app_dir / "helm_repo.yaml"
    helm_repo_path.write_text(
        """apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: HelmRepository
metadata:
  name: podinfo
  namespace: flux-system
spec:
  interval: 5m
  type: oci
  url: oci://ghcr.io/stefanprodan/charts
""",
        encoding="utf-8",
    )

    helm_release_path = app_dir / "helm_release.yaml"
    helm_release_path.write_text(
        """
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: podinfo
  namespace: podinfo
spec:
  releaseName: podinfo
  chart:
    spec:
      chart: podinfo
      sourceRef:
        kind: HelmRepository
        name: podinfo
        namespace: flux-system
""",
        encoding="utf-8",
    )

    return app_dir


async def test_kustomization_reconciliation(
    store: Store,
    controller: KustomizationController,
    fluxtomization_path: Path,
    git_repo_path: Path,
    app_dir: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Test basic kustomization reconciliation."""
    # Create a GitRepository source
    source = GitRepository(
        namespace="test-ns",
        name="git-repo",
        url="file://" + str(git_repo_path),
    )
    source_rid = NamedResource(source.kind, source.namespace, source.name)
    store.add_object(source)
    store.set_artifact(
        source_rid, GitArtifact(url=source.url, local_path=str(git_repo_path))
    )
    store.update_status(source_rid, Status.READY)

    # Create a Flux Kustomization that points to the app directory
    ks = Kustomization.parse_doc(
        yaml.load(fluxtomization_path.read_text(), Loader=yaml.SafeLoader)
    )
    rid = NamedResource(ks.kind, ks.namespace, ks.name)

    # Add the kustomization to trigger reconciliation
    store.add_object(ks)

    task_service = get_task_service()
    await task_service.block_till_done()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    artifact = store.get_artifact(rid, KustomizationArtifact)
    status = store.get_status(rid)

    assert artifact is not None, "Expected artifact to be set"
    assert artifact.path == str(app_dir), (
        f"Expected path {app_dir}, got {artifact.path}"
    )
    assert status is not None, "Expected status to be set"
    assert status.status == Status.READY, f"Expected status READY, got {status.status}"

    # Verify objects are applied
    objects = store.list_objects()
    assert [
        obj
        for obj in objects
        # GitRepository has a tmpdir random path so ignore
        if hasattr(obj, "kind") and obj.kind != "GitRepository"
    ] == snapshot


async def test_kustomization_with_oci_source(
    store: Store,
    controller: KustomizationController,
    oci_fluxtomization_path: Path,
    git_repo_path: Path,
    app_dir: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Test kustomization with OCI source reference."""
    # Add source first
    source = OCIRepository(namespace="test-ns", name="test-repo", url="test-url")
    source_rid = NamedResource(source.kind, source.namespace, source.name)
    store.add_object(source)

    # Add an OCIArtifact to simulate the source controller's behavior
    store.set_artifact(
        source_rid,
        OCIArtifact(
            url=source.url,
            local_path=str(git_repo_path),
        ),
    )
    store.update_status(source_rid, Status.READY)

    # Create a Flux Kustomization that points to the app directory
    ks = Kustomization.parse_doc(
        yaml.load(oci_fluxtomization_path.read_text(), Loader=yaml.SafeLoader)
    )
    rid = NamedResource(ks.kind, ks.namespace, ks.name)

    # Add the kustomization to trigger reconciliation
    store.add_object(ks)

    task_service = get_task_service()
    await task_service.block_till_done()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    # Verify the artifact was created with the correct path
    artifact = store.get_artifact(rid, KustomizationArtifact)
    status = store.get_status(rid)

    assert artifact is not None
    assert artifact.path == str(app_dir)
    assert status is not None
    assert status.status == Status.READY

    # Verify objects are applied
    objects = store.list_objects()
    assert [
        obj
        for obj in objects
        # GitRepository has a tmpdir random path so ignore
        if hasattr(obj, "kind") and obj.kind != "GitRepository"
    ] == snapshot


async def test_kustomization_dependencies(
    store: Store,
    controller: KustomizationController,
    git_repo_path: Path,
    app_dir: Path,
) -> None:
    """Test kustomization with dependencies."""
    # Create a GitRepository source first
    source = GitRepository(
        namespace="test-ns", name="test-repo", url="file://" + str(git_repo_path)
    )
    source_rid = NamedResource(source.kind, source.namespace, source.name)
    store.add_object(source)
    store.set_artifact(
        source_rid, GitArtifact(url=source.url, local_path=str(git_repo_path))
    )
    store.update_status(source_rid, Status.READY)

    # Add dependency kustomization
    dep = Kustomization(
        namespace="test-ns",
        name="dep-ks",
        path="./app",
        source_kind=source.kind,
        source_name=source.name,
        source_namespace=source.namespace,
        contents={
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "dep-ks", "namespace": "test-ns"},
            "spec": {
                "path": "./",
                "sourceRef": {
                    "kind": source.kind,
                    "name": source.name,
                    "namespace": source.namespace,
                },
                "interval": "5m",
            },
        },
    )
    store.add_object(dep)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert task_service.get_num_active_tasks() == 1  # Reoncile

    # Add kustomization with dependency
    ks = Kustomization(
        namespace="test-ns",
        name="test-ks",
        path="./app",
        source_kind=source.kind,
        source_name=source.name,
        source_namespace=source.namespace,
        depends_on=["test-ns/dep-ks"],
        contents={
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "test-ks", "namespace": "test-ns"},
            "spec": {
                "path": "./",
                "sourceRef": {
                    "kind": source.kind,
                    "name": source.name,
                    "namespace": source.namespace,
                },
                "dependsOn": [{"name": "dep-ks"}],
                "interval": "5m",
            },
        },
    )
    rid = NamedResource(ks.kind, ks.namespace, ks.name)
    store.add_object(ks)

    await task_service.block_till_done()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    # Verify the artifact was created with the correct path
    artifact = store.get_artifact(rid, KustomizationArtifact)
    status = store.get_status(rid)

    assert artifact is not None
    assert artifact.path == str(app_dir)
    assert status is not None
    assert status.status == Status.READY


async def test_kustomization_missing_source(
    store: Store,
    controller: KustomizationController,
) -> None:
    """Test kustomization with missing source."""
    ks = Kustomization(
        namespace="test-ns",
        name="test-ks",
        source_kind="GitRepository",
        source_name="missing-repo",
        source_namespace="test-ns",
        path="./test-path",
        contents={
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "test-ks", "namespace": "test-ns"},
            "spec": {
                "path": "./test-path",
                "sourceRef": {
                    "kind": "GitRepository",
                    "name": "missing-repo",
                    "namespace": "test-ns",
                },
                "interval": "5m",
            },
        },
    )
    rid = NamedResource(ks.kind, ks.namespace, ks.name)

    store.add_object(ks)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert task_service.get_num_active_tasks() == 1

    await asyncio.sleep(0.001)  # Allow task to run

    artifact = store.get_artifact(rid, KustomizationArtifact)
    status = store.get_status(rid)

    assert artifact is None
    assert status is not None
    assert status.status == Status.PENDING
    assert "Starting reconciliation" in (status.error or "")


async def test_kustomization_missing_dependency(
    store: Store,
    controller: KustomizationController,
    git_repo_path: Path,
    app_dir: Path,
) -> None:
    """Test kustomization with missing dependency."""
    # Create a GitRepository source first
    source = GitRepository(
        namespace="test-ns", name="test-repo", url="file://" + str(git_repo_path)
    )
    source_rid = NamedResource(source.kind, source.namespace, source.name)
    store.add_object(source)
    store.set_artifact(
        source_rid, GitArtifact(url=source.url, local_path=str(git_repo_path))
    )
    store.update_status(source_rid, Status.READY)

    # Add kustomization with missing dependency
    ks = Kustomization(
        namespace="test-ns",
        name="test-ks",
        path="./app",
        source_kind=source.kind,
        source_name=source.name,
        source_namespace=source.namespace,
        depends_on=["test-ns/missing-ks"],
        contents={
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "test-ks", "namespace": "test-ns"},
            "spec": {
                "path": "./",
                "sourceRef": {
                    "kind": source.kind,
                    "name": source.name,
                    "namespace": source.namespace,
                },
                "dependsOn": [{"name": "missing-ks"}],
                "interval": "5m",
            },
        },
    )
    rid = NamedResource(ks.kind, ks.namespace, ks.name)

    store.add_object(ks)

    task_service = get_task_service()
    await task_service.block_till_done()
    assert task_service.get_num_active_tasks() == 1
    await asyncio.sleep(0.001)  # Allow task to run
    await asyncio.sleep(0.001)  # Allow task to run

    artifact = store.get_artifact(rid, KustomizationArtifact)
    status = store.get_status(rid)

    assert artifact is None
    assert status is not None
    assert status.status == Status.PENDING
    assert "Pending dependencies: ['test-ns/missing-ks']" in (status.error or "")


async def test_kustomization_dependency_becomes_ready_later(
    store: Store,
    controller: KustomizationController,
    git_repo_path: Path,
    app_dir: Path,
) -> None:
    """Test a Kustomization whose dependency becomes ready later."""
    task_service = get_task_service()

    # 1. Create and prepare GitRepository source
    source = GitRepository(
        namespace="test-ns", name="test-repo", url="file://" + str(git_repo_path)
    )
    source_rid = NamedResource(source.kind, source.namespace, source.name)
    store.add_object(source)
    store.set_artifact(
        source_rid, GitArtifact(url=source.url, local_path=str(git_repo_path))
    )
    store.update_status(source_rid, Status.READY)
    await task_service.block_till_done()

    # 2. Create dependency Kustomization (dep_ks)
    dep_ks_name = "dep-ks"
    dep_ks = Kustomization(
        namespace="test-ns",
        name=dep_ks_name,
        path="./app",  # Relative to source_path
        source_kind=source.kind,
        source_name=source.name,
        source_namespace=source.namespace,
        contents={
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": dep_ks_name, "namespace": "test-ns"},
            "spec": {
                "path": "./app",
                "sourceRef": {
                    "kind": source.kind,
                    "name": source.name,
                    "namespace": source.namespace,
                },
                "interval": "1m",
            },
        },
    )
    dep_ks_rid = NamedResource(dep_ks.kind, dep_ks.namespace, dep_ks.name)

    # Add dep_ks to the store. It should become READY.
    store.add_object(dep_ks)
    await task_service.block_till_done()
    assert task_service.get_num_active_tasks() == 1  # Reconcile dep_ks
    await task_service.block_till_done()

    dep_status_after_add = store.get_status(dep_ks_rid)
    assert dep_status_after_add is not None
    assert dep_status_after_add.status == Status.READY
    dep_artifact_after_add = store.get_artifact(dep_ks_rid, KustomizationArtifact)
    assert dep_artifact_after_add is not None
    assert dep_artifact_after_add.path == str(app_dir)

    # 3. Manually set dep_ks status to PENDING
    store.update_status(
        dep_ks_rid, Status.PENDING, error="Simulated: dep_ks initially pending"
    )
    dep_status_after_manual_pending = store.get_status(dep_ks_rid)
    assert dep_status_after_manual_pending is not None
    assert dep_status_after_manual_pending.status == Status.PENDING

    # 4. Create main Kustomization (main_ks) depending on dep_ks
    main_ks_name = "main-ks"
    main_ks = Kustomization(
        namespace="test-ns",
        name=main_ks_name,
        path="./app",
        source_kind=source.kind,
        source_name=source.name,
        source_namespace=source.namespace,
        depends_on=[f"{dep_ks.namespace}/{dep_ks.name}"],
        contents={
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": main_ks_name, "namespace": "test-ns"},
            "spec": {
                "path": "./app",
                "sourceRef": {
                    "kind": source.kind,
                    "name": source.name,
                    "namespace": source.namespace,
                },
                "dependsOn": [{"name": dep_ks.name, "namespace": dep_ks.namespace}],
                "interval": "1m",
            },
        },
    )
    main_ks_rid = NamedResource(main_ks.kind, main_ks.namespace, main_ks.name)

    # Add main_ks. It should see dep_ks as PENDING and become PENDING.
    store.add_object(main_ks)
    await task_service.block_till_done()
    assert task_service.get_num_active_tasks() == 1  # Reconcile main_ks
    await asyncio.sleep(0.01)  # Allow task to run
    await asyncio.sleep(0.01)  # Allow task to run

    main_ks_status_initial = store.get_status(main_ks_rid)
    assert main_ks_status_initial is not None
    assert main_ks_status_initial.status == Status.PENDING
    assert dep_ks_name in (main_ks_status_initial.error or "")

    # 5. Mark dep_ks as READY again (artifact is already there from initial reconciliation)
    store.update_status(dep_ks_rid, Status.READY)
    dep_status_after_manual_ready = store.get_status(dep_ks_rid)
    assert dep_status_after_manual_ready is not None
    assert dep_status_after_manual_ready.status == Status.READY

    # 6. Wait for main_ks to potentially notice and re-reconcile.
    await task_service.block_till_done()  # Allow for any triggered tasks
    await task_service.block_till_done()  # Wait for ks build to complete

    # 7. Assert final status of main_ks.
    main_ks_status_final = store.get_status(main_ks_rid)
    assert main_ks_status_final is not None
    assert main_ks_status_final.status == Status.READY

    # Verify main_ks artifact if it became READY
    main_ks_artifact = store.get_artifact(main_ks_rid, KustomizationArtifact)
    assert main_ks_artifact is not None
    assert main_ks_artifact.path == str(app_dir)


async def test_unsupported_kind(
    store: Store,
    controller: KustomizationController,
) -> None:
    """Test that unsupported kinds are ignored."""

    class UnsupportedKind:
        kind = "UnsupportedKind"

        def __init__(self) -> None:
            self.namespace = "test-ns"
            self.name = "unsupported"

    obj = UnsupportedKind()
    rid = NamedResource(obj.kind, obj.namespace, obj.name)

    store.add_object(obj)  # type: ignore[type-var]

    task_service = get_task_service()
    await task_service.block_till_done()
    assert not task_service.get_num_active_tasks()

    artifact = store.get_artifact(rid, KustomizationArtifact)
    status = store.get_status(rid)
    assert artifact is None
    assert status is None
