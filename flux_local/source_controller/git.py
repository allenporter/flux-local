"""Git repository controller."""

import git
import logging

from flux_local.manifest import GitRepository

from .artifact import GitArtifact
from .cache import get_git_cache

_LOGGER = logging.getLogger(__name__)


class GitError(Exception):
    """Exception raised for git operations."""


async def fetch_git(obj: GitRepository) -> GitArtifact:
    """Fetch a Git repository using the cache.

    Args:
        obj: The GitRepository object containing repository details

    Returns:
        GitArtifact: Artifact containing the local path and URL

    Raises:
        GitError: If git operations fail
    """
    try:
        # Parse the reference to use in cache key
        ref_str = ""
        if obj.ref:
            if obj.ref.commit:
                ref_str = f"commit:{obj.ref.commit}"
            elif obj.ref.tag:
                ref_str = f"tag:{obj.ref.tag}"
            elif obj.ref.branch:
                ref_str = f"branch:{obj.ref.branch}"
            elif obj.ref.semver:
                ref_str = f"semver:{obj.ref.semver}"

        # Get the cache path for this repository
        cache = get_git_cache()
        repo_path = cache.get_repo_path(obj.url, ref_str)

        # Clone or update the repository
        if (repo_path / ".git").exists():
            _LOGGER.info(f"Updating existing repository at {repo_path}")
            repo = git.Repo(str(repo_path))
            repo.git.pull()
        else:
            _LOGGER.info(f"Cloning repository {obj.url} to {repo_path}")
            repo = git.Repo.clone_from(obj.url, str(repo_path))

        # Handle reference checkout based on priority: commit > tag > semver > branch
        if obj.ref:
            if obj.ref.commit:
                _LOGGER.info(f"Checking out commit {obj.ref.commit}")
                repo.git.checkout(obj.ref.commit)
            elif obj.ref.tag:
                _LOGGER.info(f"Checking out tag {obj.ref.tag}")
                try:
                    # Try to check out the tag directly
                    repo.git.checkout(obj.ref.tag)
                except git.exc.GitCommandError:
                    # If that fails, try to fetch the tag first
                    repo.git.fetch("--tags")
                    repo.git.checkout(obj.ref.tag)
            elif obj.ref.semver:
                _LOGGER.info(f"Checking out semver {obj.ref.semver}")
                # TODO: Implement semver tag filtering
                raise NotImplementedError("Semver tag filtering not implemented")
            elif obj.ref.branch:
                _LOGGER.info(f"Checking out branch {obj.ref.branch}")
                repo.git.checkout(obj.ref.branch)

        # Return the artifact with the actual path and URL
        return GitArtifact(
            path=str(repo_path),
            url=obj.url,
            tag=obj.ref.tag if obj.ref else None,  # Store tag if specified
        )

    except git.exc.GitCommandError as e:
        raise GitError(f"Git operation failed: {e}") from e
    except Exception as e:
        raise GitError(f"Failed to fetch repository: {e}") from e
