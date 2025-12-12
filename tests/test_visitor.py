"""Tests for the visitor module."""

import subprocess
import tempfile
from pathlib import Path

from flux_local import git_repo
from flux_local.visitor import HelmVisitor
from flux_local.manifest import HELM_CHART


async def test_helm_chart_resolution_with_chartref() -> None:
    """
    Test that HelmChart resources are correctly resolved when HelmRelease uses chartRef.

    This test would catch the bug where chart_visitor stores HelmChart resources
    keyed by source repository coordinates, but inflate_release looks them up using
    HelmChart resource coordinates (from chartRef).

    The test uses a HelmChart resource with a different name/namespace than its
    source repository to expose the key mismatch bug.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = Path(tmpdir)

        # Initialize a git repository (required by git_repo.build_manifest)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=test_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=test_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=test_path,
            check=True,
            capture_output=True,
        )

        # Create a HelmRepository (source repository)
        helm_repo_path = test_path / "helm-repo.yaml"
        helm_repo_path.write_text("""---
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: HelmRepository
metadata:
  name: bitnami-repo
  namespace: flux-system
spec:
  url: https://charts.bitnami.com/bitnami
  interval: 1h
""")

        # Create a HelmChart resource that references the HelmRepository
        # Note: The HelmChart resource name/namespace (nginx-chart/flux-system) is
        # different from the source repository name/namespace (bitnami-repo/flux-system)
        helm_chart_path = test_path / "helm-chart.yaml"
        helm_chart_path.write_text("""---
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: HelmChart
metadata:
  name: nginx-chart
  namespace: flux-system
spec:
  chart: nginx
  version: 15.3.0
  sourceRef:
    kind: HelmRepository
    name: bitnami-repo
    namespace: flux-system
""")

        # Create a HelmRelease that uses chartRef to reference the HelmChart resource
        helm_release_path = test_path / "helm-release.yaml"
        helm_release_path.write_text("""---
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: nginx
  namespace: default
spec:
  interval: 10m
  chartRef:
    kind: HelmChart
    name: nginx-chart
    namespace: flux-system
  values:
    replicaCount: 2
""")

        # Create a kustomize Kustomization file (required for kustomize to work)
        kustomize_kustomization_path = test_path / "kustomization.yaml"
        kustomize_kustomization_path.write_text("""---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helm-repo.yaml
  - helm-chart.yaml
  - helm-release.yaml
  - git-repo.yaml
  - flux-kustomization.yaml
""")

        # Create a Flux Kustomization resource (required for doc_visitor to work)
        # This references a GitRepository that we'll create
        flux_kustomization_path = test_path / "flux-kustomization.yaml"
        flux_kustomization_path.write_text("""---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: test-app
  namespace: flux-system
spec:
  interval: 10m
  path: .
  sourceRef:
    kind: GitRepository
    name: test-repo
    namespace: flux-system
""")

        # Create a GitRepository resource
        git_repo_path = test_path / "git-repo.yaml"
        git_repo_path.write_text("""---
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: GitRepository
metadata:
  name: test-repo
  namespace: flux-system
spec:
  url: file:///dev/null
  interval: 1h
""")

        # Commit files to git (required by git_repo.build_manifest)
        subprocess.run(
            ["git", "add", "."],
            cwd=test_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=test_path,
            check=True,
            capture_output=True,
        )

        # Set up the visitor pattern with chart_visitor
        query = git_repo.ResourceSelector()
        query.path = git_repo.PathSelector(test_path)
        query.helm_release.namespace = None  # All
        query.kustomization.namespace = "flux-system"  # Match the Kustomization namespace

        helm_visitor = HelmVisitor()
        query.helm_repo.visitor = helm_visitor.repo_visitor()
        query.helm_release.visitor = helm_visitor.release_visitor()
        query.doc_visitor = helm_visitor.chart_visitor()  # This collects HelmChart resources

        await git_repo.build_manifest(selector=query)

        # Verify that the HelmChart was collected
        assert len(helm_visitor.helm_charts) == 1

        # Verify the key is the HelmChart resource namespace/name, not source repo coordinates
        chart_key = ("flux-system", "nginx-chart")
        assert chart_key in helm_visitor.helm_charts

        helm_chart = helm_visitor.helm_charts[chart_key]
        assert helm_chart.name == "nginx-chart"  # Resource name
        assert helm_chart.namespace == "flux-system"  # Resource namespace
        assert helm_chart.chart == "nginx"  # Chart name from spec.chart
        assert helm_chart.version == "15.3.0"
        assert helm_chart.repo_name == "bitnami-repo"  # Source repository name
        assert helm_chart.repo_namespace == "flux-system"  # Source repository namespace

        # Verify that the HelmRelease was collected
        assert len(helm_visitor.releases) == 1
        release = helm_visitor.releases[0]
        assert release.name == "nginx"
        assert release.namespace == "default"

        # Before the fix, the chartRef would not be resolved because:
        # - chart_visitor stored by (repo_namespace, repo_name) = ("flux-system", "bitnami-repo")
        # - inflate_release looked up by (repo_namespace, repo_name) = ("flux-system", "nginx-chart")
        # These don't match!

        # After the fix, verify that the release's chartRef can be resolved
        # The chartRef should reference the HelmChart resource
        assert release.chart.repo_kind == HELM_CHART
        assert release.chart.repo_name == "nginx-chart"  # HelmChart resource name
        assert release.chart.repo_namespace == "flux-system"  # HelmChart resource namespace
        assert release.chart.version is None  # Not yet resolved

        # Verify that the lookup key used by inflate_release matches the storage key
        # This is the key that inflate_release will use to look up the HelmChart
        # It should match the key used by chart_visitor to store it
        chart_namespace = release.chart.repo_namespace or "flux-system"
        chart_key = (chart_namespace, release.chart.repo_name)

        assert chart_key in helm_visitor.helm_charts, (
            f"HelmChart lookup key {chart_key} not found in helm_charts. "
            f"Available keys: {list(helm_visitor.helm_charts.keys())}. "
            f"This would cause the bug where chartRef cannot resolve HelmChart resources."
        )
