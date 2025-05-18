"""
HelmRelease artifact types.

This module defines the artifact types used by the HelmRelease controller
for storing and managing Helm-related artifacts.
"""

from dataclasses import dataclass
from typing import Any

import yaml

from flux_local.store.artifact import Artifact


@dataclass(frozen=True, kw_only=True)
class HelmReleaseArtifact(Artifact):
    """
    Artifact representing a HelmRelease build result.

    Attributes:
        objects: List of rendered Kubernetes objects
        values: Resolved values used for rendering
    """

    objects: list[dict[str, Any]]
    values: dict[str, Any]

    @property
    def manifests(self) -> list[str]:
        """List of rendered Kubernetes manifests as YAML strings."""
        return [yaml.dump(obj, sort_keys=False) for obj in self.objects]
