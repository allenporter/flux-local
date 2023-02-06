"""Library for running `helm template` to produce local items in the cluster.

You can instantiate a helm template with the following:
- A HelmRepository which is a url that contains charts
- A HelmRelease which is an instance of a HelmChart in a HelmRepository

This is an example that prepares the helm repository:
```python
from flux_local.kustomize import Kustomize
from flux_local.helm import Helm
from flux_local.manifest import HelmRepository

kustomize = Kustomize.build(TESTDATA_DIR)
repos = await kustomize.grep("kind=^HelmRepository$").objects()
helm = Helm("/tmp/path/helm", "/tmp/path/cache")
for repo in repos:
  helm.add_repo(HelmRepository.from_doc(repo))
await helm.update()
```

Then to actually instantiate a template from a HelmRelease:
```python
from flux_local.manifest import HelmRelease

releases = await kustomize.grep("kind=^HelmRelease$").objects()
if not len(releases) == 1:
    raise ValueError("Expected only one HelmRelease")
tmpl = helm.template(
    HelmRelease.from_doc(releases[0]),
    releases[0]["spec"].get("values"))
objects = await tmpl.objects()
for object in objects:
    print(f"Found object {object['apiVersion']} {object['kind']}")
```
"""

import datetime
import logging
from pathlib import Path
from typing import Any

import aiofiles
import yaml

from . import command
from .kustomize import Kustomize
from .manifest import HelmRelease, HelmRepository

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
        """Return a synthetic repository config object."""
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        return {
            "apiVersion": "",
            "generated": now.isoformat(),
            "repositories": [
                {
                    "name": f"{repo.namespace}-{repo.name}",
                    "url": repo.url,
                }
                for repo in self._repos
            ],
        }


class Helm:
    """Manages local HelmRepository state."""

    def __init__(self, tmp_dir: Path, cache_dir: Path) -> None:
        """Initialize Helm."""
        self._tmp_dir = tmp_dir
        self._repo_config_file = self._tmp_dir / "repository-config.yaml"
        self._flags = [
            "--registry-config",
            "/dev/null",
            "--repository-cache",
            str(cache_dir),
            "--repository-config",
            str(self._repo_config_file),
        ]
        self._repos: list[HelmRepository] = []

    def add_repo(self, repo: HelmRepository) -> None:
        """Add the specified HelmRepository to the local config."""
        self._repos.append(repo)

    async def update(self) -> None:
        """Return command line arguments to update the local repo.

        Typically the repository must be updated before doing any chart templating.
        """
        content = yaml.dump(RepositoryConfig(self._repos).config)
        async with aiofiles.open(str(self._repo_config_file), mode="w") as config_file:
            await config_file.write(content)
        await command.run([HELM_BIN, "repo", "update"] + self._flags)

    async def template(self, release: HelmRelease, values: dict[str, Any]) -> Kustomize:
        """Return command line arguments to template the specified chart."""
        args = [
            HELM_BIN,
            "template",
            release.name,
            release.chart.chart_name,
            "--namespace",
            release.namespace,
            "--skip-crds",  # Reduce size of output
            "--version",
            release.chart.version,
        ]
        if values:
            values_path = self._tmp_dir / f"{release.release_name}-values.yaml"
            async with aiofiles.open(values_path, mode="w") as values_file:
                await values_file.write(yaml.dump(values))
            args.extend(["--values", str(values_path)])
        return Kustomize([args + self._flags])
