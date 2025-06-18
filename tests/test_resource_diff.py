"""Tests for the resource diff module."""

import pathlib
import shutil
import tempfile
from contextlib import contextmanager
from collections.abc import Generator

import yaml
from syrupy.assertion import SnapshotAssertion

from flux_local import git_repo
from flux_local.resource_diff import (
    get_helm_release_diff_keys,
    merge_helm_releases,
    merge_named_resources,
    build_helm_dependency_map,
    perform_json_diff,
    perform_yaml_diff,
)
from flux_local.visitor import ObjectOutput, HelmVisitor, ResourceKey
from flux_local.manifest import HelmRelease, HelmChart, NamedResource

TESTDATA_PATH = pathlib.Path("tests/testdata/cluster8")


async def visit_helm_content(path: pathlib.Path) -> tuple[ObjectOutput, HelmVisitor]:
    """Visit content in the specified path."""

    query = git_repo.ResourceSelector()
    query.path = git_repo.PathSelector(path)
    query.helm_release.namespace = None  # All

    content = ObjectOutput([])
    query.kustomization.visitor = content.visitor()

    helm_visitor = HelmVisitor()
    query.helm_repo.visitor = helm_visitor.repo_visitor()
    query.helm_release.visitor = helm_visitor.release_visitor()

    await git_repo.build_manifest(selector=query)

    return content, helm_visitor


@contextmanager
def mirror_worktree(path: pathlib.Path) -> Generator[pathlib.Path]:
    """Create a copy of the path in a temporary git repo for generating diffs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        # Required by `git_repo` to find the root
        shutil.copytree(".git", str(tmp_path / ".git"))

        new_path = tmp_path / path
        shutil.copytree(path, new_path)

        yield new_path


def test_no_diff_keys_empty_input() -> None:
    """Test get_helm_release_diff_keys with empty inputs."""

    a = ObjectOutput([])
    b = ObjectOutput([])
    assert get_helm_release_diff_keys(a, b, {}) == []


async def test_no_diff_keys() -> None:
    """Test get_helm_release_diff_keys with no diff."""

    content, helm_visitor = await visit_helm_content(TESTDATA_PATH)
    with mirror_worktree(TESTDATA_PATH) as new_cluster_path:
        content_new, helm_visitor_new = await visit_helm_content(new_cluster_path)

    dep_map = build_helm_dependency_map(helm_visitor, helm_visitor_new)
    assert get_helm_release_diff_keys(content, content_new, dep_map) == []


async def test_helm_release_spec_diff() -> None:
    """Test get_helm_release_diff_keys with a diff in the input values."""

    content, helm_visitor = await visit_helm_content(TESTDATA_PATH)
    with mirror_worktree(TESTDATA_PATH) as new_cluster_path:

        # Generate a diff by changing a value in the HelmRelease
        podinfo_path = new_cluster_path / "apps/podinfo.yaml"
        podinfo_doc = list(
            yaml.load_all(podinfo_path.read_text(), Loader=yaml.SafeLoader)
        )
        assert len(podinfo_doc) == 2
        assert podinfo_doc[1]["kind"] == "HelmRelease"
        assert podinfo_doc[1]["spec"]["values"]["redis"]["enabled"]
        podinfo_doc[1]["spec"]["values"]["redis"]["enabled"] = False
        podinfo_path.write_text(yaml.dump_all(podinfo_doc))

        content_new, helm_visitor_new = await visit_helm_content(new_cluster_path)

    dep_map = build_helm_dependency_map(helm_visitor, helm_visitor_new)
    assert get_helm_release_diff_keys(content, content_new, dep_map) == [
        ResourceKey(
            kustomization_path=str(TESTDATA_PATH / "apps"),
            kind="HelmRelease",
            namespace="podinfo",
            name="podinfo",
        )
    ]


async def test_helm_repository_causes_diff() -> None:
    """Test get_helm_release_diff_keys where an input HelmRepository change causes a diff."""

    content, helm_visitor = await visit_helm_content(TESTDATA_PATH)
    with mirror_worktree(TESTDATA_PATH) as new_cluster_path:

        # Generate a diff by changing a value in the HelmRelease
        podinfo_path = new_cluster_path / "apps/podinfo.yaml"
        podinfo_doc = list(
            yaml.load_all(podinfo_path.read_text(), Loader=yaml.SafeLoader)
        )
        assert len(podinfo_doc) == 2
        assert podinfo_doc[0]["kind"] == "HelmRepository"
        assert podinfo_doc[0]["spec"]["url"] == "oci://ghcr.io/stefanprodan/charts"
        podinfo_doc[0]["spec"]["url"] = "oci://ghcr.io/stefanprodan/charts2"
        podinfo_path.write_text(yaml.dump_all(podinfo_doc))

        content_new, helm_visitor_new = await visit_helm_content(new_cluster_path)

    dep_map = build_helm_dependency_map(helm_visitor, helm_visitor_new)
    assert get_helm_release_diff_keys(content, content_new, dep_map) == [
        ResourceKey(
            kustomization_path=str(TESTDATA_PATH / "apps"),
            kind="HelmRelease",
            namespace="podinfo",
            name="podinfo",
        )
    ]


CLUSTER7_PATH = pathlib.Path("tests/testdata/cluster7")


async def test_oci_repository_diff() -> None:
    """Test get_helm_release_diff_keys with a diff in the input dependencies."""

    content, helm_visitor = await visit_helm_content(CLUSTER7_PATH)
    with mirror_worktree(CLUSTER7_PATH) as new_cluster_path:

        # Generate a diff by changing a value in the HelmRelease
        charts_path = new_cluster_path / "flux/charts/app-template.yaml"
        charts_doc = list(
            yaml.load_all(charts_path.read_text(), Loader=yaml.SafeLoader)
        )
        assert len(charts_doc) == 1
        assert charts_doc[0]["kind"] == "OCIRepository"
        assert (
            charts_doc[0]["spec"]["url"] == "oci://ghcr.io/bjw-s-labs/helm/app-template"
        )
        charts_doc[0]["spec"]["ref"]["tag"] = "4.1.1"
        charts_path.write_text(yaml.dump_all(charts_doc))

        content_new, helm_visitor_new = await visit_helm_content(new_cluster_path)

    dep_map = build_helm_dependency_map(helm_visitor, helm_visitor_new)
    assert get_helm_release_diff_keys(content, content_new, dep_map) == [
        ResourceKey(
            kustomization_path=str(CLUSTER7_PATH / "flux/apps"),
            kind="HelmRelease",
            namespace="flux-system",
            name="cloudflared",
        ),
        ResourceKey(
            kustomization_path=str(CLUSTER7_PATH / "flux/apps"),
            kind="HelmRelease",
            namespace="flux-system",
            name="unifi-controller",
        ),
    ]


async def test_merge_helm_releases() -> None:
    """Test merge_helm_releases."""

    chart = HelmChart(
        name="chart", version="1.0.0", repo_name="example", repo_namespace="flux-system"
    )

    a = [
        HelmRelease(
            name="a", namespace="default", chart=chart, values={"key": "value1"}
        ),
        HelmRelease(
            name="b", namespace="default", chart=chart, values={"key": "value2"}
        ),
    ]
    b = [
        HelmRelease(
            name="b", namespace="default", chart=chart, values={"key": "value3"}
        ),
        HelmRelease(
            name="c", namespace="default", chart=chart, values={"key": "value4"}
        ),
    ]
    assert list(merge_helm_releases(a, b)) == [
        (
            HelmRelease(
                name="a", namespace="default", chart=chart, values={"key": "value1"}
            ),
            None,
        ),
        (
            HelmRelease(
                name="b", namespace="default", chart=chart, values={"key": "value2"}
            ),
            HelmRelease(
                name="b", namespace="default", chart=chart, values={"key": "value3"}
            ),
        ),
        (
            None,
            HelmRelease(
                name="c", namespace="default", chart=chart, values={"key": "value4"}
            ),
        ),
    ]


async def test_merge_named_resources() -> None:
    """Test for merging named resources."""
    a = [
        NamedResource(name="podinfo", namespace="podinfo", kind="HelmRelease"),
        NamedResource(name="podinfo", namespace="podinfo", kind="ConfigMap"),
        NamedResource(name="secret", namespace="podinfo", kind="Secret"),
    ]
    b = [
        NamedResource(name="podinfo", namespace="podinfo", kind="HelmRelease"),
        NamedResource(name="podinfo", namespace="podinfo", kind="ConfigMap"),
        NamedResource(name="podinfo", namespace="flux-system", kind="HelmRepository"),
    ]
    assert list(merge_named_resources(a, b)) == [
        NamedResource(name="podinfo", namespace="podinfo", kind="HelmRelease"),
        NamedResource(name="podinfo", namespace="podinfo", kind="ConfigMap"),
        NamedResource(name="secret", namespace="podinfo", kind="Secret"),
        NamedResource(name="podinfo", namespace="flux-system", kind="HelmRepository"),
    ]


async def test_perform_yaml_diff(snapshot: SnapshotAssertion) -> None:
    """Test perform_yaml_diff where an input HelmRepository change causes a diff."""

    content, helm_visitor = await visit_helm_content(TESTDATA_PATH)
    with mirror_worktree(TESTDATA_PATH) as new_cluster_path:

        # Generate a diff by changing a value in the HelmRelease
        podinfo_path = new_cluster_path / "apps/podinfo.yaml"
        podinfo_doc = list(
            yaml.load_all(podinfo_path.read_text(), Loader=yaml.SafeLoader)
        )
        assert len(podinfo_doc) == 2
        assert podinfo_doc[0]["kind"] == "HelmRepository"
        assert podinfo_doc[0]["spec"]["url"] == "oci://ghcr.io/stefanprodan/charts"
        podinfo_doc[0]["spec"]["url"] = "oci://ghcr.io/stefanprodan/charts2"
        podinfo_path.write_text(yaml.dump_all(podinfo_doc))

        content_new, helm_visitor_new = await visit_helm_content(new_cluster_path)

    diff = "\n".join(list(perform_yaml_diff(content, content_new, 5, 50000)))
    assert diff == snapshot


async def test_perform_json_diff(snapshot: SnapshotAssertion) -> None:
    """Test perform_yaml_diff where an input HelmRepository change causes a diff."""

    content, helm_visitor = await visit_helm_content(TESTDATA_PATH)
    with mirror_worktree(TESTDATA_PATH) as new_cluster_path:

        # Generate a diff by changing a value in the HelmRelease
        podinfo_path = new_cluster_path / "apps/podinfo.yaml"
        podinfo_doc = list(
            yaml.load_all(podinfo_path.read_text(), Loader=yaml.SafeLoader)
        )
        assert len(podinfo_doc) == 2
        assert podinfo_doc[0]["kind"] == "HelmRepository"
        assert podinfo_doc[0]["spec"]["url"] == "oci://ghcr.io/stefanprodan/charts"
        podinfo_doc[0]["spec"]["url"] = "oci://ghcr.io/stefanprodan/charts2"
        podinfo_path.write_text(yaml.dump_all(podinfo_doc))

        content_new, helm_visitor_new = await visit_helm_content(new_cluster_path)

    diff = "\n".join(list(perform_json_diff(content, content_new, 5, 50000)))
    assert diff == snapshot
