"""Git repository controller."""

from dataclasses import dataclass
import asyncio

from flux_local.manifest import BaseManifest

from .artifact import GitArtifact


@dataclass
class GitRepository(BaseManifest):
    """Placeholder until we define a real GitRepo."""


async def fetch_git(obj: GitRepository) -> GitArtifact:
    """Fetch a Git repository."""
    # Simulate IO-bound operation
    await asyncio.sleep(0)
    return GitArtifact(path="/tmp/dummy", url="https://example.com/repo.git")
