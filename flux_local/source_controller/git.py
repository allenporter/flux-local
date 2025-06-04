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
        # Get the cache path for this repository
        cache = get_git_cache()
        repo_path = cache.get_repo_path(obj.url, obj.ref.ref_str if obj.ref else None)

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
            url=obj.url,
            local_path=str(repo_path),
            ref=obj.ref,
        )

    except git.exc.GitCommandError as e:
        raise GitError(f"Git operation failed: {e}") from e
    except Exception as e:
        raise GitError(f"Failed to fetch repository: {e}") from e
