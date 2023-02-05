"""Tests for manifest library."""

import yaml

from flux_local.manifest import HelmRelease, HelmRepository


def test_parse_helm_release() -> None:
    """Test parsing a helm release doc."""

    release = HelmRelease.from_doc(
        yaml.load(
            """---
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: metallb
  namespace: metallb
spec:
  chart:
    spec:
      chart: metallb
      reconcileStrategy: ChartVersion
      sourceRef:
        kind: HelmRepository
        name: bitnami
        namespace: flux-system
      version: 4.1.14
  install:
    crds: CreateReplace
    remediation:
      retries: 3
  interval: 5m
  releaseName: metallb
  upgrade:
    crds: CreateReplace
""",
            Loader=yaml.CLoader,
        )
    )
    assert release.name == "metallb"
    assert release.namespace == "metallb"
    assert release.repo_name == "bitnami"
    assert release.repo_namespace == "flux-system"


def test_parse_helm_repository() -> None:
    """Test parsing a helm repository doc."""

    repo = HelmRepository.from_doc(
        yaml.load(
            """---
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: HelmRepository
metadata:
  name: bitnami
  namespace: flux-system
spec:
  interval: 30m
  provider: generic
  timeout: 1m0s
  url: https://charts.bitnami.com/bitnami
""",
            Loader=yaml.CLoader,
        )
    )
    assert repo.name == "bitnami"
    assert repo.namespace == "flux-system"
    assert repo.url == "https://charts.bitnami.com/bitnami"
