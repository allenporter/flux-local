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
    """Artifact representing a HelmRelease build result.

    This is written after a HelmRelease is reconciled to record the
    rendered Kubernetes objects and values used for rendering.
    """

    chart_name: str
    """HelmChart name used to render this release for informational purposes."""

    values: dict[str, Any]
    """Resolved values used for rendering the HelmRelease."""

    objects: list[dict[str, Any]]
    """The rendered Kubernetes objects, output of templating the Helm Chart."""

    @property
    def manifests(self) -> list[str]:
        """List of rendered Kubernetes manifests as YAML strings."""
        return [yaml.dump(obj, sort_keys=False) for obj in self.objects]
