"""Cache management for git repositories."""

import hashlib
import tempfile
import logging
from pathlib import Path
from shutil import rmtree

from slugify import slugify
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)


class GitError(Exception):
    """Exception raised for git operations."""


class GitCache:
    """Cache manager for git repositories.

    This cache persists for the lifetime of the process and stores
    cloned repositories in a dedicated cache directory.
    """

    def __init__(self) -> None:
        """Initialize the cache manager."""
        # Create a persistent cache directory
        self._cache_dir = Path(tempfile.gettempdir()) / "flux-local-cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._repos: dict[str, Path] = {}  # Map of URL -> local path

    def _slugify_url(self, url: str) -> str:
        """Extract and slugify a repository name from a URL.

        Args:
            url: The URL to extract the repository name from

        Returns:
            str: A slugified version of the repository name
        """
        try:
            # Parse the URL using urllib
            parsed = urlparse(url)

            # Get the path component and remove any trailing .git extension
            path = parsed.path
            if path.endswith(".git"):
                path = path[:-4]

            # Get the last component of the path (repository name)
            slug = path.split("/")[-1]

            # Handle SSH URLs (git@github.com:user/repo.git)
            if parsed.scheme == "" and "@" in url:
                # Get the part after the @
                slug = url.split("@")[1].split(":")[1].split(".")[0]

            # Slugify the name
            return slugify(slug, max_length=50, lowercase=True, separator="-")

        except ValueError as e:
            _LOGGER.error(f"Invalid URL format for {url}: {e}")
            raise GitError(f"Invalid repository URL format: {e}") from e
        except AttributeError as e:
            _LOGGER.error(f"Error accessing URL components for {url}: {e}")
            raise GitError(f"Invalid URL structure: {e}") from e
        except IndexError as e:
            _LOGGER.error(f"Error splitting URL path for {url}: {e}")
            raise GitError(f"Invalid URL path format: {e}") from e

    def get_repo_path(self, url: str, ref: str | None = None) -> Path:
        """Get the local path for a repository.

        Args:
            url: The URL of the repository
            ref: The git reference string (branch, tag, commit, etc.)

        Returns:
            Path: The local path where the repository should be cached
        """
        try:
            # Create a unique cache key using SHA-256 hash of the URL and ref
            cache_key = hashlib.sha256()
            cache_key.update(url.encode("utf-8"))
            if ref:
                cache_key.update(ref.encode("utf-8"))

            # Get the slugified repository name
            slug = self._slugify_url(url)

            # Use the first 16 characters of the hash for the directory name
            hash_str = cache_key.hexdigest()[:16]

            # Create a simple directory structure with readable slug
            # e.g. /flux-local-cache/my-repo/ab1234567890abcdef
            cache_path = self._cache_dir / slug / hash_str
            cache_path.mkdir(parents=True, exist_ok=True)

            # Store the mapping using the original URL for lookup
            self._repos[url] = cache_path
            return cache_path

        except (FileNotFoundError, PermissionError) as e:
            _LOGGER.error(f"Error creating cache directory for {url}: {e}")
            raise GitError(f"Failed to create cache directory: {e}") from e
        except OSError as e:
            _LOGGER.error(f"OS error while creating cache path for {url}: {e}")
            raise GitError(f"OS error while creating cache path: {e}") from e
        except Exception as e:
            _LOGGER.error(f"Unexpected error creating cache path for {url}: {e}")
            raise GitError(f"Unexpected error creating cache path: {e}") from e

    def cleanup(self) -> None:
        """Clean up all cached repositories."""
        try:
            for path in self._repos.values():
                if path.exists():
                    _LOGGER.info(f"Cleaning up cached repository: {path}")
                    rmtree(path)
        except Exception as e:
            _LOGGER.error(f"Error during cache cleanup: {e}")


# Create a singleton instance for the application
_git_cache = GitCache()


def get_git_cache() -> GitCache:
    """Get the singleton GitCache instance."""
    return _git_cache
