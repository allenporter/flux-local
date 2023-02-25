"""Representation of the contents of a cluster.

A manifest may be built directly from the local context of a cluster, or may be
serialized and stored and checked into the cluster for use in other applications
e.g. such as writing management plan for resources.
"""

from pathlib import Path
from typing import Any, Optional, cast

import aiofiles
import yaml
from pydantic import BaseModel, Field

__all__ = [
    "read_manifest",
    "write_manifest",
    "Manifest",
    "Cluster",
    "Kustomization",
    "HelmRepository",
    "HelmRelease",
    "HelmChart",
    "ManifestException",
]

# Match a prefix of apiVersion to ensure we have the right type of object.
# We don't check specific versions for forward compatibility on upgrade.
CLUSTER_KUSTOMIZE_DOMAIN = "kustomize.toolkit.fluxcd.io"
KUSTOMIZE_DOMAIN = "kustomize.config.k8s.io"
HELM_REPO_DOMAIN = "source.toolkit.fluxcd.io"
HELM_RELEASE_DOMAIN = "helm.toolkit.fluxcd.io"
CRD_KIND = "CustomResourceDefinition"
SECRET_KIND = "Secret"


def _check_version(doc: dict[str, Any], version: str) -> None:
    """Assert that the resource has the specified version."""
    if not (api_version := doc.get("apiVersion")):
        raise ValueError(f"Invalid object missing apiVersion: {doc}")
    if not api_version.startswith(version):
        raise ValueError(f"Invalid object expected '{version}': {doc}")


class BaseManifest(BaseModel):
    """Base class for all manifest objects."""

    _COMPACT_EXCLUDE_FIELDS: dict[str, Any] = {}

    def compact_dict(self) -> dict[str, Any]:
        """Return a compact dictionary representation of the object.

        This is similar to `dict()` but with a specific implementation for serializing
        with variable fields removed.
        """
        return self.dict(exclude=self._COMPACT_EXCLUDE_FIELDS)  # type: ignore[arg-type]

    @classmethod
    def parse_yaml(cls, content: str) -> "BaseManifest":
        """Parse a serialized manifest."""
        doc = next(yaml.load_all(content, Loader=yaml.Loader), None)
        return cls.parse_obj(doc)

    def yaml(self) -> str:
        """Return a YAML string representation of compact_dict."""
        data = self.compact_dict()
        return cast(str, yaml.dump(data, sort_keys=False, explicit_start=True))


class HelmChart(BaseManifest):
    """A representation of an instantiation of a chart for a HelmRelease."""

    name: str
    """The name of the chart within the HelmRepository."""

    version: Optional[str] = None
    """The version of the chart."""

    repo_name: str
    """The name of the HelmRepository."""

    repo_namespace: str
    """The namespace of the HelmRepository."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "HelmChart":
        """Parse a HelmChart from a HelmRelease resource object."""
        _check_version(doc, HELM_RELEASE_DOMAIN)
        if not (spec := doc.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec: {doc}")
        if not (chart := spec.get("chart")):
            raise ValueError(f"Invalid {cls} missing spec.chart: {doc}")
        if not (chart_spec := chart.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec.chart.spec: {doc}")
        if not (chart := chart_spec.get("chart")):
            raise ValueError(f"Invalid {cls} missing spec.chart.spec.chart: {doc}")
        version = chart_spec.get("version")
        if not (source_ref := chart_spec.get("sourceRef")):
            raise ValueError(f"Invalid {cls} missing spec.chart.spec.sourceRef: {doc}")
        if "namespace" not in source_ref or "name" not in source_ref:
            raise ValueError(f"Invalid {cls} missing sourceRef fields: {doc}")
        return cls(
            name=chart,
            version=version,
            repo_name=source_ref["name"],
            repo_namespace=source_ref["namespace"],
        )

    @property
    def chart_name(self) -> str:
        """Identifier for the HelmChart."""
        return f"{self.repo_namespace}-{self.repo_name}/{self.name}"

    _COMPACT_EXCLUDE_FIELDS = {"version": True}


class HelmRelease(BaseManifest):
    """A representation of a Flux HelmRelease."""

    name: str
    """The name of the HelmRelease."""

    namespace: str
    """The namespace that owns the HelmRelease."""

    chart: HelmChart
    """A mapping to a specific helm chart for this HelmRelease."""

    values: Optional[dict[str, Any]] = None
    """The values to install in the chart."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "HelmRelease":
        """Parse a HelmRelease from a kubernetes resource object."""
        _check_version(doc, HELM_RELEASE_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise ValueError(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise ValueError(f"Invalid {cls} missing metadata.namespace: {doc}")
        chart = HelmChart.parse_doc(doc)
        return cls(
            name=name,
            namespace=namespace,
            chart=chart,
            values=doc["spec"].get("values"),
        )

    @property
    def release_name(self) -> str:
        """Identifier for the HelmRelease."""
        return f"{self.namespace}-{self.name}"

    _COMPACT_EXCLUDE_FIELDS = {
        "values": True,
        "chart": HelmChart._COMPACT_EXCLUDE_FIELDS,
    }


class HelmRepository(BaseManifest):
    """A representation of a flux HelmRepository."""

    name: str
    """The name of the HelmRepository."""

    namespace: str
    """The namespace of owning the HelmRepository."""

    url: str
    """The URL to the repository of helm charts."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "HelmRepository":
        """Parse a HelmRepository from a kubernetes resource."""
        _check_version(doc, HELM_REPO_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise ValueError(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise ValueError(f"Invalid {cls} missing metadata.namespace: {doc}")
        if not (spec := doc.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec: {doc}")
        if not (url := spec.get("url")):
            raise ValueError(f"Invalid {cls} missing spec.url: {doc}")
        return cls(name=name, namespace=namespace, url=url)

    @property
    def repo_name(self) -> str:
        """Identifier for the HelmRepository."""
        return f"{self.namespace}-{self.name}"


class Kustomization(BaseManifest):
    """A Kustomization is a set of declared cluster artifacts.

    This represents a flux Kustomization that points to a path that
    contains typical `kustomize` Kustomizations on local disk that
    may be flat or contain overlays.
    """

    name: str
    """The name of the kustomization."""

    namespace: str | None = None
    """The namespace of the kustomization."""

    path: str
    """The local repo path to the kustomization contents."""

    helm_repos: list[HelmRepository] = Field(default_factory=list)
    """The set of HelmRepositories represented in this kustomization."""

    helm_releases: list[HelmRelease] = Field(default_factory=list)
    """The set of HelmRelease represented in this kustomization."""

    source_path: str | None = None
    """Optional source path for this Kustomization, relative to the build path."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "Kustomization":
        """Parse a partial Kustomization from a kubernetes resource."""
        _check_version(doc, CLUSTER_KUSTOMIZE_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise ValueError(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise ValueError(f"Invalid {cls} missing metadata.namespace: {doc}")
        if not (spec := doc.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec: {doc}")
        if not (path := spec.get("path")):
            raise ValueError(f"Invalid {cls} missing spec.path: {doc}")
        source_path = metadata.get("annotations", {}).get("config.kubernetes.io/path")
        return Kustomization(
            name=name, namespace=namespace, path=path, source_path=source_path
        )

    @property
    def id_name(self) -> str:
        """Identifier for the Kustomization in tests"""
        return f"{self.path}"

    _COMPACT_EXCLUDE_FIELDS = {
        "helm_releases": {
            "__all__": HelmRelease._COMPACT_EXCLUDE_FIELDS,
        },
        "source_path": True,
    }


class Cluster(BaseManifest):
    """A set of nodes that run containerized applications.

    Many flux git repos will only have a single flux cluster, though
    a repo may also contain multiple (e.g. dev an prod).
    """

    name: str
    """The name of the cluster."""

    namespace: str
    """The namespace of the cluster."""

    path: str
    """The local git repo path to the Kustomization objects for the cluster."""

    kustomizations: list[Kustomization] = Field(default_factory=list)
    """A list of flux Kustomizations for the cluster."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "Cluster":
        """Parse a partial Kustomization from a kubernetes resource."""
        _check_version(doc, CLUSTER_KUSTOMIZE_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise ValueError(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise ValueError(f"Invalid {cls} missing metadata.namespace: {doc}")
        if not (spec := doc.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec: {doc}")
        if not (path := spec.get("path")):
            raise ValueError(f"Invalid {cls} missing spec.path: {doc}")
        return Cluster(name=name, namespace=namespace, path=path)

    @property
    def id_name(self) -> str:
        """Identifier for the Cluster in tests."""
        return f"{self.path}"

    @property
    def helm_repos(self) -> list[HelmRepository]:
        """Return the list of HelmRepository objects from all Kustomizations."""
        return [
            repo
            for kustomization in self.kustomizations
            for repo in kustomization.helm_repos
        ]

    @property
    def helm_releases(self) -> list[HelmRelease]:
        """Return the list of HelmRelease objects from all Kustomizations."""
        return [
            release
            for kustomization in self.kustomizations
            for release in kustomization.helm_releases
        ]

    _COMPACT_EXCLUDE_FIELDS = {
        "kustomizations": {
            "__all__": Kustomization._COMPACT_EXCLUDE_FIELDS,
        }
    }


class Manifest(BaseManifest):
    """Holds information about cluster and applications contained in a repo."""

    clusters: list[Cluster]
    """A list of Clusters represented in the repo."""

    _COMPACT_EXCLUDE_FIELDS = {
        "clusters": {
            "__all__": Cluster._COMPACT_EXCLUDE_FIELDS,
        }
    }


class ManifestException(Exception):
    """Error raised while working with the Manifest."""


async def read_manifest(manifest_path: Path) -> Manifest:
    """Return the contents of a serialized manifest file.

    A manifest file is typically created by `flux-local get cluster -o yaml` or
    similar command.
    """
    async with aiofiles.open(str(manifest_path)) as manifest_file:
        content = await manifest_file.read()
        return cast(Manifest, Manifest.parse_yaml(content))


async def write_manifest(manifest_path: Path, manifest: Manifest) -> None:
    """Write the specified manifest content to disk."""
    content = manifest.yaml()
    async with aiofiles.open(str(manifest_path), mode="w") as manifest_file:
        await manifest_file.write(content)


async def update_manifest(manifest_path: Path, manifest: Manifest) -> None:
    """Write the specified manifest only if changed."""
    new_content = manifest.yaml()
    async with aiofiles.open(str(manifest_path)) as manifest_file:
        content = await manifest_file.read()
    if content == new_content:
        return
    async with aiofiles.open(str(manifest_path), mode="w") as manifest_file:
        await manifest_file.write(new_content)
