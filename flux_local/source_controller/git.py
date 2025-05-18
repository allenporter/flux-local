"""Git repository controller."""

import asyncio

from flux_local.manifest import GitRepository
from .artifact import GitArtifact


async def fetch_git(obj: GitRepository) -> GitArtifact:
    """Fetch a Git repository."""
    # Simulate IO-bound operation
    await asyncio.sleep(0)
    return GitArtifact(path="/tmp/dummy", url="https://example.com/repo.git")
