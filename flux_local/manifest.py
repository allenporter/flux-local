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


class HelmChart(BaseModel):
    """A representation of an instantiation of a chart for a HelmRelease."""

    name: str
    """The name of the chart within the HelmRepository."""

    version: str
    """The version of the chart."""

    repo_name: str
    """The name of the HelmRepository."""

    repo_namespace: str
    """The namespace of the HelmRepository."""

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "HelmChart":
        """Parse a HelmChart from a HelmRelease resource object."""
        if not (spec := doc.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec: {doc}")
        if not (chart := spec.get("chart")):
            raise ValueError(f"Invalid {cls} missing spec.chart: {doc}")
        if not (chart_spec := chart.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec.chart.spec: {doc}")
        if not (chart := chart_spec.get("chart")):
            raise ValueError(f"Invalid {cls} missing spec.chart.spec.chart: {doc}")
        if not (version := chart_spec.get("version")):
            raise ValueError(f"Invalid {cls} missing spec.chart.spec.version: {doc}")
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


class HelmRelease(BaseModel):
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
    def from_doc(cls, doc: dict[str, Any]) -> "HelmRelease":
        """Parse a HelmRelease from a kubernetes resource object."""
        if not (metadata := doc.get("metadata")):
            raise ValueError(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise ValueError(f"Invalid {cls} missing metadata.namespace: {doc}")
        chart = HelmChart.from_doc(doc)
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


class HelmRepository(BaseModel):
    """A representation of a flux HelmRepository."""

    name: str
    """The name of the HelmRepository."""

    namespace: str
    """The namespace of owning the HelmRepository."""

    url: str
    """The URL to the repository of helm charts."""

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "HelmRepository":
        """Parse a HelmRepository from a kubernetes resource."""
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


class Kustomization(BaseModel):
    """A Kustomization is a set of declared cluster artifacts.

    This represents a flux Kustomization that points to a path that
    contains typical `kustomize` Kustomizations on local disk that
    may be flat or contain overlays.
    """

    name: str
    """The name of the kustomization."""

    path: str
    """The local repo path to the kustomization."""

    helm_repos: list[HelmRepository] = Field(default_factory=list)
    """The set of HelmRepositories represented in this kustomization."""

    helm_releases: list[HelmRelease] = Field(default_factory=list)
    """The set of HelmRelease represented in this kustomization."""

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "Kustomization":
        """Parse a partial Kustomization from a kubernetes resource."""
        if not (metadata := doc.get("metadata")):
            raise ValueError(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid {cls} missing metadata.name: {doc}")
        if not (spec := doc.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec: {doc}")
        if not (path := spec.get("path")):
            raise ValueError(f"Invalid {cls} missing spec.path: {doc}")
        return Kustomization(name=name, path=path)

    @property
    def id_name(self) -> str:
        """Identifier for the Kustomization in tests"""
        return f"{self.path}"


class Cluster(BaseModel):
    """A set of nodes that run containerized applications.

    Many flux git repos will only have a single flux cluster, though
    a repo may also contain multiple (e.g. dev an prod).
    """

    name: str
    """The name of the cluster."""

    path: str
    """The local git repo path to the Kustomization objects for the cluster."""

    kustomizations: list[Kustomization] = Field(default_factory=list)
    """A list of flux Kustomizations for the cluster."""

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "Cluster":
        """Parse a partial Kustomization from a kubernetes resource."""
        if not (metadata := doc.get("metadata")):
            raise ValueError(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid {cls} missing metadata.name: {doc}")
        if not (spec := doc.get("spec")):
            raise ValueError(f"Invalid {cls} missing spec: {doc}")
        if not (path := spec.get("path")):
            raise ValueError(f"Invalid {cls} missing spec.path: {doc}")
        return Cluster(name=name, path=path)

    @property
    def id_name(self) -> str:
        """Identifier for the Cluster in tests."""
        return f"{self.path}"


class Manifest(BaseModel):
    """Holds information about cluster and applications contained in a repo."""

    clusters: list[Cluster]
    """A list of Clusters represented in the repo."""

    @staticmethod
    def parse_yaml(content: str) -> "Manifest":
        """Parse a serialized manifest."""
        doc = next(yaml.load_all(content, Loader=yaml.Loader), None)
        if not doc or "spec" not in doc:
            raise ManifestException("Manifest file malformed, missing 'spec'")
        return Manifest(clusters=doc["spec"])

    def yaml(self) -> str:
        """Serialize the manifest as a yaml file.

        The contents of the cluster will be compacted to remove values that
        exist in the live cluster but do not make sense to be persisted in the
        manifest on disk.
        """
        data = self.dict(
            exclude={
                "clusters": {
                    "__all__": {
                        "kustomizations": {
                            "__all__": {
                                "helm_releases": {
                                    "__all__": {
                                        "values": True,
                                        "chart": {
                                            "version": True,
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }
        )
        return cast(
            str,
            yaml.dump({"spec": data["clusters"]}, sort_keys=False, explicit_start=True),
        )


class ManifestException(Exception):
    """Error raised while working with the Manifest."""


async def read_manifest(manifest_path: Path) -> Manifest:
    """Return the contents of a serialized manifest file."""
    async with aiofiles.open(str(manifest_path)) as manifest_file:
        content = await manifest_file.read()
        return Manifest.parse_yaml(content)


async def write_manifest(manifest_path: Path, manifest: Manifest) -> None:
    """Write the specified manifest content to disk."""
    content = manifest.yaml()
    async with aiofiles.open(str(manifest_path), mode="w") as manifest_file:
        await manifest_file.write(content)


async def update_manifest(manifest_path: Path, manifest: Manifest) -> None:
    """Write the specified manifest only if changed."""
    async with aiofiles.open(str(manifest_path)) as manifest_file:
        content = await manifest_file.read()
    new_content = manifest.yaml()
    if content == new_content:
        return
    async with aiofiles.open(str(manifest_path), mode="w") as manifest_file:
        await manifest_file.write(new_content)
