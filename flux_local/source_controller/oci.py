"""OCI repository controller."""

import logging

from flux_local.manifest import OCIRepository
from oras.client import OrasClient

from .artifact import OCIArtifact
from .cache import get_git_cache
from .secret import Auth

_LOGGER = logging.getLogger(__name__)


async def fetch_oci(obj: OCIRepository, auth: Auth | None = None) -> OCIArtifact:
    """Fetch an OCI repository."""
    cache = get_git_cache()
    oci_repo_path = cache.get_repo_path(obj.url, obj.version())

    _LOGGER.info("Fetching OCI repository %s", obj)
    client = OrasClient()
    if auth:
        _LOGGER.info("Using authentication for OCI repository %s", obj.url)
        client.login(hostname=obj.url, username=auth.username, password=auth.password)
    res = client.pull(target=obj.versioned_url(), outdir=str(oci_repo_path))
    _LOGGER.debug("Downloaded resources: %s", res)
    return OCIArtifact(
        url=obj.url,
        ref=obj.ref,
        local_path=str(oci_repo_path),
    )
