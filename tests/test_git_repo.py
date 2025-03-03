"""Tests for git_repo."""

from collections.abc import Sequence
import io
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from syrupy.assertion import SnapshotAssertion

from flux_local.git_repo import (
    build_manifest,
    ResourceSelector,
    ResourceVisitor,
    Source,
    PathSelector,
    is_allowed_source,
    adjust_ks_path,
)
from flux_local.kustomize import Kustomize, Stash
from flux_local.command import Task, run_piped
from flux_local.manifest import Kustomization
from flux_local.context import trace

TESTDATA = Path("tests/testdata/cluster")
TESTDATA_FULL_PATH = Path.cwd() / TESTDATA


async def test_build_manifest(snapshot: SnapshotAssertion) -> None:
    """Tests for building the manifest."""

    manifest = await build_manifest(TESTDATA)
    assert manifest.compact_dict() == snapshot


async def test_cluster_selector_disabled(snapshot: SnapshotAssertion) -> None:
    """Tests for building the manifest."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.cluster.enabled = False

    manifest = await build_manifest(selector=query)
    assert manifest.compact_dict() == snapshot


async def test_kustomization_selector_disabled(snapshot: SnapshotAssertion) -> None:
    """Tests for building the manifest."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.kustomization.enabled = False

    manifest = await build_manifest(selector=query)
    assert manifest.compact_dict() == snapshot


async def test_helm_release_selector_disabled(snapshot: SnapshotAssertion) -> None:
    """Tests for building the manifest with helm releases disabled."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.helm_release.enabled = False

    manifest = await build_manifest(selector=query)
    assert manifest.compact_dict() == snapshot


async def test_helm_repo_selector_disabled(snapshot: SnapshotAssertion) -> None:
    """Tests for building the manifest with helm repos disabled."""

    query = ResourceSelector()
    query.path.path = TESTDATA
    query.helm_repo.enabled = False

    manifest = await build_manifest(selector=query)
    assert manifest.compact_dict() == snapshot


async def test_kustomization_visitor(snapshot: SnapshotAssertion) -> None:
    """Tests for visiting Kustomizations."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    stream = io.StringIO()
    visits: list[tuple[str, str, str]] = []

    async def write(x: Path, y: Any, cmd: Kustomize | None) -> None:
        visits.append((str(x), y.namespace, y.name))
        if cmd:
            stream.write(await cmd.run())

    query.kustomization.visitor = ResourceVisitor(func=write)

    manifest = await build_manifest(selector=query)
    assert manifest.compact_dict() == snapshot

    visits.sort()
    assert visits == snapshot

    content = stream.getvalue()
    assert content
    assert "kind: HelmRelease" in content
    assert "name: metallb" in content


async def test_helm_repo_visitor(snapshot: SnapshotAssertion) -> None:
    """Tests for visiting a HelmRepository objects."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    visits: list[tuple[str, str, str]] = []

    async def append(x: Path, y: Any, z: Any) -> None:
        visits.append((str(x), y.namespace, y.name))

    query.helm_repo.visitor = ResourceVisitor(func=append)

    manifest = await build_manifest(selector=query)
    assert manifest.compact_dict() == snapshot

    visits.sort()
    assert visits == snapshot


async def test_helm_release_visitor(snapshot: SnapshotAssertion) -> None:
    """Tests for visiting a HelmRelease objects."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    visits: list[tuple[str, str, str]] = []

    async def append(x: Path, y: Any, z: Any) -> None:
        visits.append((str(x), y.namespace, y.name))

    query.helm_release.visitor = ResourceVisitor(
        func=append,
    )

    manifest = await build_manifest(selector=query)
    assert manifest.compact_dict() == snapshot

    visits.sort()
    assert visits == snapshot


def test_source() -> None:
    """Test parsing a source from a string."""
    source = Source.from_str("cluster=./k8s")
    assert source.name == "cluster"
    assert source.namespace is None
    assert str(source.root) == "k8s"


def test_source_with_namespace() -> None:
    """Test parsing a source from a string."""
    source = Source.from_str("flux-system2/cluster=./k8s")
    assert source.name == "cluster"
    assert source.namespace == "flux-system2"
    assert str(source.root) == "k8s"


def test_source_without_path() -> None:
    """Test parsing a source without a path."""
    source = Source.from_str("cluster")
    assert source.name == "cluster"
    assert source.namespace is None
    assert source.root is None


def test_is_allowed_source() -> None:
    """Test GitRepository sources allowed."""
    ks = Kustomization(
        name="ks",
        namespace="flux-system",
        path="./kubernetes/apps/volsync/volsync/app",
        source_name="flux-system",
    )
    assert is_allowed_source([Source.from_str("flux-system")])(ks)


def test_is_not_allowed_source() -> None:
    """Test GitRepository sources allowed."""
    ks = Kustomization(
        name="ks",
        namespace="flux-system",
        path="./kubernetes/apps/volsync/volsync/app",
        source_name="flux-system",
    )
    assert not is_allowed_source([Source.from_str("flux-system-other")])(ks)


def test_is_allowed_source_namespace_optional() -> None:
    """Test GitRepository sources allowed."""
    ks = Kustomization(
        name="ks",
        namespace="flux-system",
        path="./kubernetes/apps/volsync/volsync/app",
        source_name="flux-system",
        source_namespace="flux-system2",
    )
    assert is_allowed_source([Source.from_str("flux-system")])(ks)
    assert is_allowed_source([Source.from_str("flux-system2/flux-system")])(ks)
    assert not is_allowed_source([Source.from_str("flux-system3/flux-system")])(ks)


@pytest.mark.parametrize(
    ("path", "sources"),
    [
        ("tests/testdata/cluster", None),
        ("tests/testdata/cluster2", None),
        (
            "tests/testdata/cluster3",
            [Source.from_str("cluster=tests/testdata/cluster3")],
        ),
        ("tests/testdata/cluster4", None),
        ("tests/testdata/cluster5", None),
        ("tests/testdata/cluster6", None),
        ("tests/testdata/cluster7", None),
    ],
    ids=[
        "cluster",
        "cluster2",
        "cluster3",
        "cluster4",
        "cluster5",
        "cluster6",
        "cluster7",
    ],
)
async def test_internal_commands(
    path: str, sources: list[Source] | None, snapshot: SnapshotAssertion
) -> None:
    """Tests that trace internal command run for each step."""

    context_cmds: dict[str, Any] = {}

    async def piped_recorder(tasks: Sequence[Task]) -> str:
        """Record commands run with the trace context."""

        piped_cmds = []
        for task in tasks:
            if isinstance(task, Stash):
                continue
            piped_cmds.append(str(task))

        c = context_cmds
        for level in trace.get(["root"]):
            if level not in c:
                c[level] = {}
            c = c[level]
        c["cmds"] = c.get("cmds", []) + piped_cmds
        return await run_piped(tasks)

    selector = ResourceSelector(path=PathSelector(path=Path(path), sources=sources))
    with patch("flux_local.kustomize.run_piped", side_effect=piped_recorder):
        await build_manifest(selector=selector)

    assert context_cmds == snapshot


@pytest.mark.parametrize(
    ("path", "expected_path"),
    [
        ("kubernetes/apps/example", "kubernetes/apps/example"),
        ("./kubernetes/apps/example", "kubernetes/apps/example"),
        ("/kubernetes/apps/example", "kubernetes/apps/example"),
        ("", str(TESTDATA)),
    ],
)
def test_adjust_ks_path(path: str, expected_path: str) -> None:
    """Test adjusting the Kustomization path relative directory."""
    ks = Kustomization(
        name="ks",
        namespace="flux-system",
        path=path,
        source_name="flux-system",
    )
    selector = PathSelector(TESTDATA_FULL_PATH)
    assert adjust_ks_path(ks, selector) == Path(expected_path)


async def test_kustomization_label_selector() -> None:
    """Tests for building the manifest."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    async def get_ks() -> list[str]:
        manifest = await build_manifest(selector=query)
        return [
            ks.namespaced_name
            for cluster in manifest.clusters
            for ks in cluster.kustomizations
        ]

    assert await get_ks() == [
        "flux-system/apps",
        "flux-system/flux-system",
        "flux-system/infra-configs",
        "flux-system/infra-controllers",
    ]

    query.kustomization.label_selector = {"app.kubernetes.io/name": "apps"}
    assert await get_ks() == [
        "flux-system/apps",
    ]

    query.kustomization.label_selector = {"app.kubernetes.io/name": "podinfo"}
    assert await get_ks() == []

    # Match on multiple fields
    query.kustomization.label_selector = {
        "app.kubernetes.io/name": "apps",
        "app.kubernetes.io/instance": "apps",
    }
    assert await get_ks() == [
        "flux-system/apps",
    ]

    # Mismatch on one field
    query.kustomization.label_selector = {
        "app.kubernetes.io/name": "apps",
        "app.kubernetes.io/instance": "flux-system",
    }
    assert await get_ks() == []


async def test_helmrelease_label_selector() -> None:
    """Tests for building the manifest."""

    query = ResourceSelector()
    query.path.path = TESTDATA

    async def get_hr() -> list[str]:
        manifest = await build_manifest(selector=query)
        return [
            hr.namespaced_name
            for cluster in manifest.clusters
            for hr in cluster.helm_releases
        ]

    assert await get_hr() == [
        "podinfo/podinfo",
        "metallb/metallb",
        "flux-system/weave-gitops",
    ]

    query.helm_release.label_selector = {"app.kubernetes.io/name": "podinfo"}
    assert await get_hr() == [
        "podinfo/podinfo",
    ]

    query.helm_release.label_selector = {
        "app.kubernetes.io/name": "kubernetes-dashboard"
    }
    assert await get_hr() == []
