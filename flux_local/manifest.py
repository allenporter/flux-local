"""Representation of the contents of a cluster.

A manifest may be built directly from the local context of a cluster, or may be
serialized and stored and checked into the cluster for use in other applications
e.g. such as writing management plan for resources.
"""

import base64
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Optional, cast, ClassVar

import aiofiles
from mashumaro.codecs.yaml import yaml_decode, yaml_encode
from mashumaro import DataClassDictMixin, field_options
from mashumaro.config import BaseConfig

from .exceptions import InputException

__all__ = [
    "read_manifest",
    "write_manifest",
    "Manifest",
    "Cluster",
    "Kustomization",
    "HelmRepository",
    "HelmRelease",
    "HelmChart",
    "ConfigMap",
    "Secret",
]

_LOGGER = logging.getLogger(__name__)


# Match a prefix of apiVersion to ensure we have the right type of object.
# We don't check specific versions for forward compatibility on upgrade.
FLUXTOMIZE_DOMAIN = "kustomize.toolkit.fluxcd.io"
KUSTOMIZE_DOMAIN = "kustomize.config.k8s.io"
HELM_REPO_DOMAIN = "source.toolkit.fluxcd.io"
HELM_RELEASE_DOMAIN = "helm.toolkit.fluxcd.io"
GIT_REPOSITORY_DOMAIN = "source.toolkit.fluxcd.io"
OCI_REPOSITORY_DOMAIN = "source.toolkit.fluxcd.io"
CRD_KIND = "CustomResourceDefinition"
SECRET_KIND = "Secret"
CONFIG_MAP_KIND = "ConfigMap"
DEFAULT_NAMESPACE = "flux-system"
VALUE_PLACEHOLDER_TEMPLATE = "..PLACEHOLDER_{name}.."
HELM_RELEASE = "HelmRelease"
HELM_REPO_KIND = "HelmRepository"
HELM_CHART = "HelmChart"
GIT_REPOSITORY = "GitRepository"
HELM_REPOSITORY = "HelmRepository"
OCI_REPOSITORY = "OCIRepository"
KUSTOMIZE_KIND = "Kustomization"


REPO_TYPE_DEFAULT = "default"
REPO_TYPE_OCI = "oci"

# Strip any annotations from kustomize that contribute to diff noise when
# objects are re-ordered in the output
STRIP_ATTRIBUTES = [
    "config.kubernetes.io/index",
    "internal.config.kubernetes.io/index",
]


def _check_version(doc: dict[str, Any], version: str) -> None:
    """Assert that the resource has the specified version."""
    if not (api_version := doc.get("apiVersion")):
        raise InputException(f"Invalid object missing apiVersion: {doc}")
    if not api_version.startswith(version):
        raise InputException(f"Invalid object expected '{version}': {doc}")


@dataclass
class BaseManifest(DataClassDictMixin):
    """Base class for all manifest objects."""

    def compact_dict(self) -> dict[str, Any]:
        """Return a compact dictionary representation of the object.

        This is similar to `dict()` but with a specific implementation for serializing
        with variable fields removed.
        """
        return self.to_dict()

    @classmethod
    def parse_yaml(cls, content: str) -> "BaseManifest":
        """Parse a serialized manifest."""
        return yaml_decode(content, cls)

    def yaml(self, exclude: dict[str, Any] | None = None) -> str:
        """Return a YAML string representation of compact_dict."""
        return yaml_encode(self, self.__class__)  # type: ignore[return-value]

    class Config(BaseConfig):
        omit_none = True


@dataclass(frozen=True, order=True)
class NamedResource:
    """Identifier for a kubernetes resource."""

    kind: str
    namespace: str | None
    name: str

    @property
    def namespaced_name(self) -> str:
        if self.namespace:
            return f"{self.namespace}/{self.name}"
        return self.name

    def __str__(self) -> str:
        """Return the kind and namespaced name concatenated as an id."""
        return f"{self.kind}/{self.namespaced_name}"


@dataclass
class RawObject(BaseManifest):
    """Raw kubernetes object."""

    kind: str
    """The kind of the object."""

    api_version: str
    """The apiVersion of the object."""

    name: str
    """The name of the object."""

    namespace: str | None
    """The namespace of the object."""

    spec: dict[str, Any] | None = None
    """The spec of the object."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "RawObject":
        """Parse a RawObject from a raw kubernetes object."""
        if not (api_version := doc.get("apiVersion")):
            raise InputException("Invalid object missing apiVersion: {doc}")
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid object missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid object missing metadata.name: {doc}")
        return cls(
            kind=doc["kind"],
            api_version=api_version,
            name=name,
            namespace=metadata.get("namespace", DEFAULT_NAMESPACE),
            spec=doc.get("spec", {}),
        )


@dataclass
class HelmChart(BaseManifest):
    """A representation of an instantiation of a chart for a HelmRelease."""

    kind: ClassVar[str] = HELM_CHART
    """The kind of the object."""

    name: str
    """The name of the chart within the HelmRepository."""

    version: Optional[str] = field(metadata={"serialize": "omit"})
    """The version of the chart."""

    repo_name: str
    """The short name of the repository."""

    repo_namespace: str
    """The namespace of the repository."""

    repo_kind: str = HELM_REPO_KIND
    """The kind of the soruceRef of the repository (e.g. HelmRepository, GitRepository)."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any], default_namespace: str) -> "HelmChart":
        """Parse a HelmChart from a HelmRelease resource object."""
        _check_version(doc, HELM_RELEASE_DOMAIN)
        if not (spec := doc.get("spec")):
            raise InputException(f"Invalid {cls} missing spec: {doc}")
        chart_ref = spec.get("chartRef")
        chart = spec.get("chart")
        if not chart_ref and not chart:
            raise InputException(
                f"Invalid {cls} missing spec.chart or spec.chartRef: {doc}"
            )
        if chart_ref:
            if not (kind := chart_ref.get("kind")):
                raise InputException(f"Invalid {cls} missing spec.chartRef.kind: {doc}")
            if not (name := chart_ref.get("name")):
                raise InputException(f"Invalid {cls} missing spec.chartRef.name: {doc}")

            return cls(
                name=name,
                version=None,
                repo_name=name,
                repo_namespace=chart_ref.get("namespace", default_namespace),
                repo_kind=kind,
            )
        if not (chart_spec := chart.get("spec")):
            raise InputException(f"Invalid {cls} missing spec.chart.spec: {doc}")
        if not (chart := chart_spec.get("chart")):
            raise InputException(f"Invalid {cls} missing spec.chart.spec.chart: {doc}")
        version = chart_spec.get("version")
        if not (source_ref := chart_spec.get("sourceRef")):
            raise InputException(
                f"Invalid {cls} missing spec.chart.spec.sourceRef: {doc}"
            )
        if "name" not in source_ref:
            raise InputException(f"Invalid {cls} missing sourceRef fields: {doc}")
        return cls(
            name=chart,
            version=version,
            repo_name=source_ref["name"],
            repo_namespace=source_ref.get("namespace", default_namespace),
            repo_kind=source_ref.get("kind", HELM_REPO_KIND),
        )

    @property
    def repo_full_name(self) -> str:
        """Identifier for the HelmRepository."""
        return f"{self.repo_namespace}-{self.repo_name}"

    @property
    def chart_name(self) -> str:
        """Identifier for the HelmChart."""
        return f"{self.repo_full_name}/{self.name}"


@dataclass
class ValuesReference(BaseManifest):
    """A reference to a resource containing values for a HelmRelease."""

    kind: str
    """The kind of resource."""

    name: str
    """The name of the resource."""

    values_key: str = field(
        metadata=field_options(alias="valuesKey"), default="values.yaml"
    )
    """The key in the resource that contains the values."""

    target_path: Optional[str] = field(
        metadata=field_options(alias="targetPath"), default=None
    )
    """The path in the HelmRelease values to store the values."""

    optional: bool = False
    """Whether the reference is optional."""


@dataclass
class LocalObjectReference(BaseManifest):
    """A reference to a local object."""

    name: str
    """The name of the object."""


@dataclass
class HelmRelease(BaseManifest):
    """A representation of a Flux HelmRelease."""

    kind: ClassVar[str] = HELM_RELEASE
    """The kind of the object."""

    name: str
    """The name of the HelmRelease."""

    namespace: str
    """The namespace that owns the HelmRelease."""

    chart: HelmChart
    """A mapping to a specific helm chart for this HelmRelease."""

    target_namespace: str | None = field(metadata={"serialize": "omit"}, default=None)
    """The namespace to target when performing the operation."""

    values: Optional[dict[str, Any]] = field(
        metadata={"serialize": "omit"}, default=None
    )
    """The values to install in the chart."""

    values_from: Optional[list[ValuesReference]] = field(
        metadata={"serialize": "omit"}, default=None
    )
    """A list of values to reference from an ConfigMap or Secret."""

    images: list[str] | None = field(default=None)
    """The list of images referenced in the HelmRelease."""

    labels: dict[str, str] | None = field(metadata={"serialize": "omit"}, default=None)
    """A list of labels on the HelmRelease."""

    disable_schema_validation: bool = field(
        metadata={"serialize": "omit"}, default=False
    )
    """Prevents Helm from validating the values against the JSON Schema."""

    disable_openapi_validation: bool = field(
        metadata={"serialize": "omit"}, default=False
    )
    """Prevents Helm from validating the values against the Kubernetes OpenAPI Schema."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "HelmRelease":
        """Parse a HelmRelease from a kubernetes resource object."""
        _check_version(doc, HELM_RELEASE_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise InputException(f"Invalid {cls} missing metadata.namespace: {doc}")
        chart = HelmChart.parse_doc(doc, namespace)
        spec = doc["spec"]
        values_from: list[ValuesReference] | None = None
        if values_from_dict := spec.get("valuesFrom"):
            values_from = [
                ValuesReference.from_dict(subdoc) for subdoc in values_from_dict
            ]
        disable_schema_validation = any(
            bag.get("disableSchemaValidation")
            for key in ("install", "upgrade")
            if (bag := spec.get(key)) is not None
        )
        disable_openapi_validation = any(
            bag.get("disableOpenAPIValidation")
            for key in ("install", "upgrade")
            if (bag := spec.get(key)) is not None
        )
        return HelmRelease(
            name=name,
            namespace=namespace,
            target_namespace=spec.get("targetNamespace"),
            chart=chart,
            values=spec.get("values"),
            values_from=values_from,
            labels=metadata.get("labels"),
            disable_schema_validation=disable_schema_validation,
            disable_openapi_validation=disable_openapi_validation,
        )

    @property
    def release_name(self) -> str:
        """Identifier for the HelmRelease."""
        return f"{self.namespace}-{self.name}"

    @property
    def release_namespace(self) -> str:
        """Actual namespace where the HelmRelease will be installed to."""
        if self.target_namespace:
            return self.target_namespace
        return self.namespace

    @property
    def repo_name(self) -> str:
        """Identifier for the HelmRepository identified in the HelmChart."""
        return f"{self.chart.repo_namespace}-{self.chart.repo_name}"

    @property
    def namespaced_name(self) -> str:
        """Return the namespace and name concatenated as an id."""
        return f"{self.namespace}/{self.name}"

    @property
    def resource_dependencies(self) -> list[NamedResource]:
        """Return the list of input dependencies for the HelmRelease."""
        deps = [
            NamedResource(
                kind=HELM_RELEASE,
                name=self.name,
                namespace=self.namespace,
            )
        ]
        if self.chart:
            deps.append(
                NamedResource(
                    kind=self.chart.repo_kind,
                    name=self.chart.repo_name,
                    namespace=self.chart.repo_namespace,
                )
            )
        names_seen = set()
        for ref in self.values_from or ():
            if ref.name in names_seen:
                continue
            names_seen.add(ref.name)
            deps.append(
                NamedResource(kind=ref.kind, name=ref.name, namespace=self.namespace)
            )
        return deps


@dataclass
class HelmRepository(BaseManifest):
    """A representation of a flux HelmRepository."""

    kind: ClassVar[str] = HELM_REPO_KIND
    """The kind of the object."""

    name: str
    """The name of the HelmRepository."""

    namespace: str
    """The namespace of owning the HelmRepository."""

    url: str
    """The URL to the repository of helm charts."""

    repo_type: str | None = None
    """The type of the HelmRepository."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "HelmRepository":
        """Parse a HelmRepository from a kubernetes resource."""
        _check_version(doc, HELM_REPO_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise InputException(f"Invalid {cls} missing metadata.namespace: {doc}")
        if not (spec := doc.get("spec")):
            raise InputException(f"Invalid {cls} missing spec: {doc}")
        if not (url := spec.get("url")):
            raise InputException(f"Invalid {cls} missing spec.url: {doc}")
        return cls(
            name=name,
            namespace=namespace,
            url=url,
            repo_type=spec.get("type", REPO_TYPE_DEFAULT),
        )

    @property
    def repo_name(self) -> str:
        """Identifier for the HelmRepository."""
        return f"{self.namespace}-{self.name}"

    def helm_chart_name(self, chart: HelmChart) -> str:
        """Get the name or URL for a specific chart for helm template."""
        if self.repo_type == REPO_TYPE_OCI:
            # For OCI repositories, we need to append the chart short name to the URL
            return f"{self.url}/{chart.name}"
        # For regular helm repositories, we just return the full chart name
        return chart.chart_name


@dataclass
class GitRepositoryRef:
    """GitRepositoryRef defines the Git ref used for pull and checkout operations."""

    branch: str | None = field(default=None)
    """The Git branch to checkout, defaults to master."""

    tag: str | None = field(default=None)
    """The Git tag to checkout."""

    semver: str | None = field(default=None)
    """The Git tag semver expression."""

    commit: str | None = field(default=None)
    """The Git commit SHA to checkout."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "GitRepositoryRef":
        """Parse a GitRepositoryRef from a kubernetes resource."""
        return cls(
            branch=doc.get("branch"),
            tag=doc.get("tag"),
            semver=doc.get("semver"),
            commit=doc.get("commit"),
        )

    @property
    def ref_str(self) -> str | None:
        """Get the reference string for the GitRepository."""
        if self.commit:
            return f"commit:{self.commit}"
        if self.tag:
            return f"tag:{self.tag}"
        if self.branch:
            return f"branch:{self.branch}"
        if self.semver:
            return f"semver:{self.semver}"
        return None

    class Config(BaseConfig):
        omit_none = True


@dataclass
class GitRepository(BaseManifest):
    """GitRepository represents a Git repository."""

    kind: ClassVar[str] = GIT_REPOSITORY
    """The kind of the object."""

    name: str
    """The name of the GitRepository."""

    namespace: str
    """The namespace of owning the GitRepository."""

    url: str
    """The URL to the repository."""

    ref: GitRepositoryRef | None = None
    """The Git reference to use for pull and checkout operations."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "GitRepository":
        """Parse a GitRepxository from a kubernetes resource."""
        _check_version(doc, GIT_REPOSITORY_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise InputException(f"Invalid {cls} missing metadata.namespace: {doc}")
        if not (spec := doc.get("spec")):
            raise InputException(f"Invalid {cls} missing spec: {doc}")
        if not (url := spec.get("url")):
            raise InputException(f"Invalid {cls} missing spec.url: {doc}")

        ref = None
        if ref_dict := spec.get("ref"):
            ref = GitRepositoryRef.parse_doc(ref_dict)

        return cls(
            name=name,
            namespace=namespace,
            url=url,
            ref=ref,
        )

    @property
    def repo_name(self) -> str:
        """Identifier for the GitRepository."""
        return f"{self.namespace}-{self.name}"


@dataclass
class OCIRepositoryRef:
    """OCIRepositoryRef defines the image reference for the OCIRepository's URL."""

    digest: str | None = None
    """The image digest to pull, takes precedence over SemVer."""

    tag: str | None = None
    """The image tag to pull, defaults to latest."""

    semver: str | None = None
    """The range of tags to pull selecting the latest within the range."""

    semver_filter: str | None = None
    """A regex pattern to filter the tags within the SemVer range."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "OCIRepositoryRef":
        """Parse a dictionary into an OCIRepositoryRef."""
        return cls(
            digest=doc.get("digest"),
            tag=doc.get("tag"),
            semver=doc.get("semver"),
            semver_filter=doc.get("semverFilter"),
        )

    class Config(BaseConfig):
        omit_none = True


@dataclass
class OCIRepository(BaseManifest):
    """A representation of a flux OCIRepository."""

    kind: ClassVar[str] = OCI_REPOSITORY
    """The kind of the object."""

    name: str
    """The name of the OCIRepository."""

    namespace: str
    """The namespace of owning the OCIRepository."""

    url: str
    """The URL to the repository."""

    ref: OCIRepositoryRef | None = None
    """The OCI reference (tag or digest) to use."""

    secret_ref: LocalObjectReference | None = field(
        metadata=field_options(alias="secretRef"), default=None
    )
    """The local secret reference."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "OCIRepository":
        """Parse a HelmRepository from a kubernetes resource."""
        _check_version(doc, OCI_REPOSITORY_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise InputException(f"Invalid {cls} missing metadata.namespace: {doc}")
        if not (spec := doc.get("spec")):
            raise InputException(f"Invalid {cls} missing spec: {doc}")
        if not (url := spec.get("url")):
            raise InputException(f"Invalid {cls} missing spec.url: {doc}")
        repo_ref: OCIRepositoryRef | None = None
        if ref := spec.get("ref"):
            repo_ref = OCIRepositoryRef.parse_doc(ref)
        secret_ref: LocalObjectReference | None = None
        if secret_ref_dict := spec.get("secretRef"):
            secret_ref = LocalObjectReference.from_dict(secret_ref_dict)
        return cls(
            name=name,
            namespace=namespace,
            url=url,
            ref=repo_ref,
            secret_ref=secret_ref,
        )

    def version(self) -> str | None:
        """Get the version of the OCI repository."""
        if self.ref is not None:
            if self.ref.semver_filter:
                raise ValueError(
                    f"OCIRepository has unsupported field semvar_filter: {self.ref}"
                )
            if self.ref.digest:
                return self.ref.digest
            if self.ref.tag:
                return self.ref.tag
            if self.ref.semver:
                return self.ref.semver
        return None

    def versioned_url(self) -> str:
        """Get the URL with the version."""
        if self.ref is None:
            return self.url
        if self.ref.digest:
            return f"{self.url}@{self.ref.digest}"
        if self.ref.tag:
            return f"{self.url}:{self.ref.tag}"
        if self.ref.semver:
            return f"{self.url}:{self.ref.semver}"
        return self.url

    @property
    def repo_name(self) -> str:
        """Identifier for the OCIRepository."""
        return f"{self.namespace}-{self.name}"


@dataclass
class ConfigMap(BaseManifest):
    """A ConfigMap is an API object used to store data in key-value pairs."""

    kind: ClassVar[str] = CONFIG_MAP_KIND
    """The kind of the ConfigMap."""

    name: str
    """The name of the ConfigMap."""

    namespace: str | None = None
    """The namespace of the kustomization."""

    data: dict[str, Any] | None = field(metadata={"serialize": "omit"}, default=None)
    """The data in the ConfigMap."""

    binary_data: dict[str, Any] | None = field(
        metadata={"serialize": "omit"}, default=None
    )
    """The binary data in the ConfigMap."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "ConfigMap":
        """Parse a config map object from a kubernetes resource."""
        _check_version(doc, "v1")
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid {cls} missing metadata.name: {doc}")
        namespace = metadata.get("namespace")
        return ConfigMap(
            name=name,
            namespace=namespace,
            data=doc.get("data"),
            binary_data=doc.get("binaryData"),
        )


@dataclass
class Secret(BaseManifest):
    """A Secret contains a small amount of sensitive data."""

    kind: ClassVar[str] = SECRET_KIND
    """The kind of the Secret."""

    name: str
    """The name of the Secret."""

    namespace: str | None = None
    """The namespace of the Secret."""

    data: dict[str, Any] | None = field(metadata={"serialize": "omit"}, default=None)
    """The data in the Secret."""

    string_data: dict[str, Any] | None = field(
        metadata={"serialize": "omit"}, default=None
    )
    """The string data in the Secret."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any], *, wipe_secrets: bool = True) -> "Secret":
        """Parse a secret object from a kubernetes resource."""
        _check_version(doc, "v1")
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid {cls} missing metadata.name: {doc}")
        namespace = metadata.get("namespace")
        # While secrets are not typically stored in the cluster, we replace with
        # placeholder values anyway.
        data = doc.get("data")
        if data and wipe_secrets:
            for key, value in data.items():
                data[key] = base64.b64encode(
                    VALUE_PLACEHOLDER_TEMPLATE.format(name=key).encode()
                )
        string_data = doc.get("stringData")
        if string_data and wipe_secrets:
            for key, value in string_data.items():
                string_data[key] = VALUE_PLACEHOLDER_TEMPLATE.format(name=key)
        return Secret(
            name=name, namespace=namespace, data=data, string_data=string_data
        )


@dataclass
class SubstituteReference(BaseManifest):
    """SubstituteReference contains a reference to a resource containing the variables name and value."""

    kind: str
    """The kind of resource."""

    name: str
    """The name of the resource."""

    optional: bool = False
    """Whether the reference is optional."""


@dataclass
class Kustomization(BaseManifest):
    """A Kustomization is a set of declared cluster artifacts.

    This represents a flux Kustomization that points to a path that
    contains typical `kustomize` Kustomizations on local disk that
    may be flat or contain overlays.
    """

    kind: ClassVar[str] = KUSTOMIZE_KIND
    """The kind of the object."""

    name: str
    """The name of the kustomization."""

    namespace: str | None
    """The namespace of the kustomization."""

    path: str
    """The local repo path to the kustomization contents."""

    helm_repos: list[HelmRepository] = field(default_factory=list)
    """The set of HelmRepositories represented in this kustomization."""

    oci_repos: list[OCIRepository] = field(default_factory=list)
    """The set of OCIRepositories represented in this kustomization."""

    helm_releases: list[HelmRelease] = field(default_factory=list)
    """The set of HelmRelease represented in this kustomization."""

    config_maps: list[ConfigMap] = field(default_factory=list)
    """The list of config maps referenced in the kustomization."""

    secrets: list[Secret] = field(default_factory=list)
    """The list of secrets referenced in the kustomization."""

    source_path: str | None = field(metadata={"serialize": "omit"}, default=None)
    """Optional source path for this Kustomization, relative to the build path."""

    source_kind: str | None = field(metadata={"serialize": "omit"}, default=None)
    """The sourceRef kind that provides this Kustomization e.g. GitRepository etc."""

    source_name: str | None = field(metadata={"serialize": "omit"}, default=None)
    """The name of the sourceRef that provides this Kustomization."""

    source_namespace: str | None = field(metadata={"serialize": "omit"}, default=None)
    """The namespace of the sourceRef that provides this Kustomization."""

    target_namespace: str | None = field(metadata={"serialize": "omit"}, default=None)
    """The namespace to target when performing the operation."""

    contents: dict[str, Any] | None = field(
        metadata={"serialize": "omit"}, default=None
    )
    """Contents of the raw Kustomization document."""

    images: list[str] | None = field(default=None)
    """The list of images referenced in the kustomization."""

    postbuild_substitute: Optional[dict[str, Any]] = field(
        metadata={"serialize": "omit"}, default=None
    )
    """A map of key/value pairs to substitute into the final YAML manifest, after building."""

    postbuild_substitute_from: Optional[list[SubstituteReference]] = field(
        metadata={"serialize": "omit"}, default=None
    )
    """A list of substitutions to reference from an ConfigMap or Secret."""

    depends_on: list[str] | None = field(metadata={"serialize": "omit"}, default=None)
    """A list of namespaced names that this Kustomization depends on."""

    labels: dict[str, str] | None = field(metadata={"serialize": "omit"}, default=None)
    """A list of labels on the Kustomization."""

    @classmethod
    def parse_doc(cls, doc: dict[str, Any]) -> "Kustomization":
        """Parse a partial Kustomization from a kubernetes resource."""
        _check_version(doc, FLUXTOMIZE_DOMAIN)
        if not (metadata := doc.get("metadata")):
            raise InputException(f"Invalid {cls} missing metadata: {doc}")
        if not (name := metadata.get("name")):
            raise InputException(f"Invalid {cls} missing metadata.name: {doc}")
        if not (namespace := metadata.get("namespace")):
            raise InputException(f"Invalid {cls} missing metadata.namespace: {doc}")
        if not (spec := doc.get("spec")):
            raise InputException(f"Invalid {cls} missing spec: {doc}")
        path = spec.get("path", "")
        source_path = metadata.get("annotations", {}).get("config.kubernetes.io/path")
        source_ref = spec.get("sourceRef", {})
        postbuild = spec.get("postBuild", {})
        substitute_from: list[SubstituteReference] | None = None
        if substitute_from_dict := postbuild.get("substituteFrom"):
            substitute_from = [
                SubstituteReference(**subdoc) for subdoc in substitute_from_dict
            ]
        depends_on = []
        for dependency in spec.get("dependsOn", ()):
            if not (dep_name := dependency.get("name")):
                raise InputException(f"Invalid {cls} missing dependsOn.name: {doc}")
            dep_namespace = dependency.get("namespace", namespace)
            depends_on.append(f"{dep_namespace}/{dep_name}")
        return Kustomization(
            name=name,
            namespace=namespace,
            path=path,
            source_path=source_path,
            source_kind=source_ref.get("kind"),
            source_name=source_ref.get("name"),
            source_namespace=source_ref.get("namespace", namespace),
            target_namespace=spec.get("targetNamespace"),
            contents=doc,
            postbuild_substitute=postbuild.get("substitute"),
            postbuild_substitute_from=substitute_from,
            depends_on=depends_on,
            labels=metadata.get("labels"),
        )

    @property
    def id_name(self) -> str:
        """Identifier for the Kustomization in tests"""
        return f"{self.path}"

    @property
    def namespaced_name(self) -> str:
        """Return the namespace and name concatenated as an id."""
        return f"{self.namespace}/{self.name}"

    def validate_depends_on(self, all_ks: set[str]) -> None:
        """Validate depends_on values are all correct given the list of Kustomizations."""
        depends_on = set(self.depends_on or {})
        if missing := (depends_on - all_ks):
            _LOGGER.warning(
                "Kustomization %s has dependsOn with invalid names: %s",
                self.namespaced_name,
                missing,
            )
            self.depends_on = list(depends_on - missing)

    def update_postbuild_substitutions(self, substitutions: dict[str, Any]) -> None:
        """Update the postBuild.substitutions in the extracted values and raw doc contents."""
        if self.postbuild_substitute is None:
            self.postbuild_substitute = {}
        self.postbuild_substitute.update(substitutions)
        if self.contents:
            post_build = self.contents["spec"]["postBuild"]
            if (substitute := post_build.get("substitute")) is None:
                substitute = {}
                post_build["substitute"] = substitute
            substitute.update(substitutions)


@dataclass
class Cluster(BaseManifest):
    """A set of nodes that run containerized applications.

    Many flux git repos will only have a single flux cluster, though
    a repo may also contain multiple (e.g. dev an prod).
    """

    path: str
    """The local git repo path to the Kustomization objects for the cluster."""

    kustomizations: list[Kustomization] = field(default_factory=list)
    """A list of flux Kustomizations for the cluster."""

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
    def oci_repos(self) -> list[OCIRepository]:
        """Return the list of OCIRepository objects from all Kustomizations."""
        return [
            repo
            for kustomization in self.kustomizations
            for repo in kustomization.oci_repos
        ]

    @property
    def helm_releases(self) -> list[HelmRelease]:
        """Return the list of HelmRelease objects from all Kustomizations."""
        return [
            release
            for kustomization in self.kustomizations
            for release in kustomization.helm_releases
        ]


@dataclass
class Manifest(BaseManifest):
    """Holds information about cluster and applications contained in a repo."""

    clusters: list[Cluster]
    """A list of Clusters represented in the repo."""


async def read_manifest(manifest_path: Path) -> Manifest:
    """Return the contents of a serialized manifest file.

    A manifest file is typically created by `flux-local get cluster -o yaml` or
    similar command.
    """
    async with aiofiles.open(str(manifest_path)) as manifest_file:
        content = await manifest_file.read()
        if not content:
            raise ValueError("validation error for Manifest file {manifest_path}")
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


def parse_raw_obj(obj: dict[str, Any], *, wipe_secrets: bool = True) -> BaseManifest:
    """Parse a raw kubernetes object into a BaseManifest."""
    if not (kind := obj.get("kind")):
        raise InputException(f"Invalid object missing kind: {obj}")
    if not (api_version := obj.get("apiVersion")):
        raise InputException(f"Invalid object missing apiVersion: {obj}")
    if kind == KUSTOMIZE_KIND and api_version.startswith(FLUXTOMIZE_DOMAIN):
        return Kustomization.parse_doc(obj)
    if kind == HELM_RELEASE:
        return HelmRelease.parse_doc(obj)
    if kind == HELM_REPOSITORY:
        return HelmRepository.parse_doc(obj)
    if kind == GIT_REPOSITORY:
        return GitRepository.parse_doc(obj)
    if kind == OCI_REPOSITORY:
        return OCIRepository.parse_doc(obj)
    if kind == CONFIG_MAP_KIND:
        return ConfigMap.parse_doc(obj)
    if kind == SECRET_KIND:
        return Secret.parse_doc(obj, wipe_secrets=wipe_secrets)
    return RawObject.parse_doc(obj)


def is_kustomization(obj: dict[str, Any]) -> bool:
    """Check if the object is a Kustomization."""
    return obj.get("kind") == KUSTOMIZE_KIND and obj.get("apiVersion", "").startswith(
        KUSTOMIZE_DOMAIN
    )


def _strip_attrs(metadata: dict[str, Any], strip_attributes: list[str]) -> None:
    """Update the resource object, stripping any requested labels to simplify diff."""

    for attr_key in ("annotations", "labels"):
        if not (val := metadata.get(attr_key)):
            continue
        for key in strip_attributes:
            if key in val:
                del val[key]
            if not val:
                del metadata[attr_key]
                break


def strip_resource_attributes(
    resource: dict[str, Any], strip_attributes: list[str]
) -> None:
    """Strip any annotations from kustomize that contribute to diff noise when objects are re-ordered in the output."""
    _strip_attrs(resource["metadata"], strip_attributes)
    # Remove common noisy labels in commonly used templates
    if (
        (spec := resource.get("spec"))
        and (templ := spec.get("template"))
        and (meta := templ.get("metadata"))
    ):
        _strip_attrs(meta, strip_attributes)
    if (
        resource["kind"] == "List"
        and (items := resource.get("items"))
        and isinstance(items, list)
    ):
        for item in items:
            if not (item_meta := item.get("metadata")):
                continue
            _strip_attrs(item_meta, strip_attributes)
