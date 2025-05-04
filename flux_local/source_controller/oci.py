"""OCI repository controller."""

import asyncio
import logging

from flux_local.manifest import OCIRepository

from .artifact import OCIArtifact

_LOGGER = logging.getLogger(__name__)


async def fetch_oci(obj: OCIRepository) -> OCIArtifact:
    """Fetch an OCI repository."""
    _LOGGER.info("Fetching OCI repository %s", obj)
    await asyncio.sleep(0)
    return OCIArtifact(
        path="/tmp/oci", digest="sha256:dummy", url="oci://example.com/repo"
    )
