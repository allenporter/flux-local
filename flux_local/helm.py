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
  helm.add_repo(HelmRepository.parse_doc(repo))
await helm.update()
```

Then to actually instantiate a template from a HelmRelease:
```python
from flux_local.manifest import HelmRelease

releases = await kustomize.grep("kind=^HelmRelease$").objects()
if not len(releases) == 1:
    raise ValueError("Expected only one HelmRelease")
tmpl = helm.template(HelmRelease.parse_doc(releases[0]))
objects = await tmpl.objects()
for object in objects:
    print(f"Found object {object['apiVersion']} {object['kind']}")
```
"""

import datetime
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import aiofiles
import yaml

from . import command
from .kustomize import Kustomize
from .manifest import HelmRelease, HelmRepository, CRD_KIND, SECRET_KIND, REPO_TYPE_OCI
from .exceptions import HelmException

__all__ = [
    "Helm",
    "Options",
]

_LOGGER = logging.getLogger(__name__)


HELM_BIN = "helm"


def _chart_name(repo: HelmRepository, release: HelmRelease) -> str:
    """Return the helm chart name used for the helm template command."""
    if repo.repo_type == REPO_TYPE_OCI:
        return f"{repo.url}/{release.chart.name}"
    return release.chart.chart_name


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


@dataclass
class Options:
    """Options to use when inflating a Helm chart.

    Internally, these translate into either command line flags or
    resource kinds that will be filtered form the output.
    """

    skip_crds: bool = True
    """Skip CRDs when building the output."""

    skip_tests: bool = True
    """Don't run helm tests on the output."""

    skip_secrets: bool = False
    """Don't emit secrets in the output."""

    kube_version: str | None = None
    """Value of the helm --kube-version flag."""

    api_versions: str | None = None
    """Value of the helm --api-versions flag."""

    @property
    def template_args(self) -> list[str]:
        """Helm template CLI arguments built from the options."""
        args = []
        if self.skip_crds:
            args.append("--skip-crds")
        if self.skip_tests:
            args.append("--skip-tests")
        if self.kube_version:
            args.extend(["--kube-version", self.kube_version])
        if self.api_versions:
            args.extend(["--api-versions", self.api_versions])
        return args

    @property
    def skip_resources(self) -> list[str]:
        """A list of CRD resources to filter from the output."""
        skips = []
        if self.skip_crds:
            skips.append(CRD_KIND)
        if self.skip_secrets:
            skips.append(SECRET_KIND)
        return skips


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

    def add_repos(self, repos: list[HelmRepository]) -> None:
        """Add the specified HelmRepository to the local config."""
        for repo in repos:
            self._repos.append(repo)

    async def update(self) -> None:
        """Return command line arguments to update the local repo.

        Typically the repository must be updated before doing any chart templating.
        """
        _LOGGER.debug("Updating %d repositories", len(self._repos))
        repos = [repo for repo in self._repos if repo.repo_type != REPO_TYPE_OCI]
        if not repos:
            return
        content = yaml.dump(RepositoryConfig(repos).config, sort_keys=False)
        async with aiofiles.open(str(self._repo_config_file), mode="w") as config_file:
            await config_file.write(content)
        await command.run(
            command.Command(
                [HELM_BIN, "repo", "update"] + self._flags, exc=HelmException
            )
        )

    async def template(
        self,
        release: HelmRelease,
        options: Options | None = None,
    ) -> Kustomize:
        """Return command line arguments to template the specified chart.

        The default values will come from the `HelmRelease`, though you can
        also specify values directory if not present in cluster manifest
        e.g. it came from a truncated yaml.
        """
        if options is None:
            options = Options()
        repo = next(
            iter([repo for repo in self._repos if repo.repo_name == release.repo_name]),
            None,
        )
        if not repo:
            raise HelmException(
                f"Unable to find HelmRepository for {release.chart.chart_name} for "
                f"HelmRelease {release.name}"
            )
        args: list[str] = [
            HELM_BIN,
            "template",
            release.name,
            _chart_name(repo, release),
            "--namespace",
            release.namespace,
        ]
        args.extend(options.template_args)
        if release.chart.version:
            args.extend(
                [
                    "--version",
                    release.chart.version,
                ]
            )
        if release.values:
            values_path = self._tmp_dir / f"{release.release_name}-values.yaml"
            async with aiofiles.open(values_path, mode="w") as values_file:
                await values_file.write(yaml.dump(release.values, sort_keys=False))
            args.extend(["--values", str(values_path)])
        cmd = Kustomize([command.Command(args + self._flags, exc=HelmException)])
        if options.skip_resources:
            cmd = cmd.skip_resources(options.skip_resources)
        return cmd
