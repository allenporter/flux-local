"""
This library represents dataclasses for objects in the cluster.

The dataclasses are used to represent objects in the cluster such as flux Kustomizations, HelmRepositories and HelmReleases.
"""

from dataclasses import dataclass


@dataclass
class FluxKustomization:
    """
    This class represents a flux kustomization.

    A flux kustomization is a kustomization that is used by flux to manage a cluster.
    """

    name: str
    namespace: str
    path: str


@dataclass
class HelmRepository:
    """
    This class represents a helm repository.

    A helm repository is a repository of helm charts.
    """

    name: str
    url: str


@dataclass
class HelmRelease:
    """
    This class represents a helm release.

    A helm release is a release of a helm chart.
    """

    name: str
    chart: str
    namespace: str
    values: dict
