"""Artifact representation."""

from dataclasses import dataclass

from flux_local.store.artifact import Artifact


@dataclass(frozen=True, kw_only=True)
class GitArtifact(Artifact):
    """Git artifact."""

    url: str


@dataclass(frozen=True, kw_only=True)
class OCIArtifact(Artifact):
    """OCI artifact."""

    path: str
    digest: str
    url: str
