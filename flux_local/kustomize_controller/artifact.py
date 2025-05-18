"""Artifact types for the Kustomize Controller."""

from dataclasses import dataclass, field
from typing import Any

from flux_local.store.artifact import Artifact


@dataclass(frozen=True, kw_only=True)
class KustomizationArtifact(Artifact):
    """Artifact representing the result of building a Kustomization.

    Attributes:
        path: Path to the source directory containing kustomization.yaml
        manifests: List of rendered Kubernetes manifests
        revision: Source revision (commit SHA) if applicable
    """

    path: str
    manifests: list[dict[str, Any]] = field(default_factory=list)
    revision: str | None = None
