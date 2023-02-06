"""Library for running `helm template` to produce local items in the cluster."""

import datetime
import logging
from pathlib import Path
from typing import Any, Generator

import aiofiles
import yaml

from .manifest import HelmRepository, HelmRelease
from .kustomize import Kustomize
from . import command


__all__ = [
  "Helm",
]

_LOGGER = logging.getLogger(__name__)


HELM_BIN = "helm"


class RepositoryConfig:
    """Generates a helm repository configuration from flux HelmRepository objects."""

    def __init__(self, repos: list[HelmRepository]) -> None:
        """Initialize RepositoryConfig."""
        self._repos = repos

    @property
    def config(self) -> dict[str, Any]:
        """Return a synthetic repostory config object."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        return {
            "apiVersion": "",
            "generated": now.isoformat(),
            "repositories": [
                {
                    "name": f"{repo.namespace}-{repo.name}",
                    "url": repo.url,
                } for repo in self._repos
            ],
        }


class Helm:
    """Manages local HelmRepository state."""

    def __init__(self, tmp_dir: Path, cache_dir: Path) -> None:
        """Initialize Helm."""
        self._tmp_dir = tmp_dir
        self._repo_config_file = self._tmp_dir / "repository-config.yaml"
        self._flags = [
            "--registry-config", "/dev/null",
            "--repository-cache", str(cache_dir),
            "--repository-config", str(self._repo_config_file),
        ]
        self._repos: list[HelmRepository] = []

    def add_repo(self, repo: HelmRepository):
        """Add the specified HelmRepository to the local config."""
        self._repos.append(repo)

    async def update(self) -> None:
        """Return command line arguments to update the local repo.

        Typically the repository must be updated before doing any chart templating.
        """
        content = yaml.dump(RepositoryConfig(self._repos).config)
        async with aiofiles.open(str(self._repo_config_file), mode="w") as f:
            await f.write(content)
        await command.run([ HELM_BIN, "repo", "update" ] + self._flags)

    async def template(
        self,
        release: HelmRelease,
        values: dict[str, Any]
    ) -> Kustomize:
        """Return command line arguments to template the specified chart."""
        args = [
            HELM_BIN,
            "template",
            release.name,
            release.chart.chart_name,
            "--namespace", release.namespace,
            "--skip-crds",  # Reduce size of output
            "--version", release.chart.version,
        ]
        if values:
            values_file = tmp_config_path / f"{release.release_name}-values.yaml"
            async with aiofiles.open(values_file, mode="w") as f:
                await f.write(yaml.dump(values))
            args.extend(["--values", str(values_file)])
        return Kustomize([args + self._flags])
