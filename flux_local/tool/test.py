"""Flux local test action."""

import logging
import pathlib
import tempfile
from pathlib import Path

import nest_asyncio
import pytest

from flux_local import git_repo

_LOGGER = logging.getLogger(__name__)


MANIFEST_FILE = "manifest.yaml"


class TestAction:
    """Flux-local test action."""

    async def run(  # type: ignore[no-untyped-def]
        self,
        path: pathlib.Path,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = await git_repo.build_manifest(path)
            manifest_file = Path(tmp_dir) / MANIFEST_FILE
            manifest_file.write_text(manifest.yaml())
            nest_asyncio.apply()
            pytest.main(
                [
                    "-v",  # Add a line for every test
                    "--no-header",
                    # Load this module only from under pytest
                    "-p",
                    "flux_local.tool.test_kustomization",
                    str(manifest_file),
                ]
            )
