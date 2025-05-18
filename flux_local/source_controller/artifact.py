"""Artifact representation."""

from dataclasses import dataclass

from flux_local.store.artifact import Artifact
from flux_local.manifest import OCIRepositoryRef, GitRepositoryRef


@dataclass(frozen=True, kw_only=True)
class GitArtifact(Artifact):
    """Git artifact.

    This object is written to the store when the GitRepository is reconciled.
    The path references a local filesystem path at the specified ref of the git
    repo.
    """

    url: str
    """URL of the git repository, for informational/logging purposes."""

    local_path: str
    """Local filesystem path to the git repository."""

    ref: GitRepositoryRef | None = None
    """Information about the version of the git repository."""


@dataclass(frozen=True, kw_only=True)
class OCIArtifact(Artifact):
    """OCI artifact.

    This object is written when the OCIRepository is reconciled. The path
    references a local filesystem path at the specified ref of the OCI repo.
    """

    url: str
    """URL of the oci repository, for informational/logging purposes."""

    local_path: str
    """Local filesystem path to the OCI repository."""

    ref: OCIRepositoryRef | None = None
    """Information about the version of the OCI repository."""
