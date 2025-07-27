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

from collections.abc import Generator
from contextlib import contextmanager
import contextvars
import datetime
from dataclasses import dataclass
import logging
from pathlib import Path
import tempfile
from typing import Any

import aiofiles
from aiofiles.ospath import exists
import yaml

from . import command
from .kustomize import Kustomize
from .manifest import (
    HelmRelease,
    HelmRepository,
    OCIRepository,
    CRD_KIND,
    SECRET_KIND,
    REPO_TYPE_OCI,
    HELM_REPO_KIND,
    GIT_REPOSITORY,
    OCI_REPOSITORY,
    GitRepository,
)
from .source_controller.artifact import GitArtifact
from .exceptions import HelmException

__all__ = [
    "Helm",
    "Options",
]

_LOGGER = logging.getLogger(__name__)


HELM_BIN = "helm"

_config_context = contextvars.ContextVar[str | None]("config_context", default=None)


@dataclass(kw_only=True, frozen=True)
class LocalGitRepository:
    """A GitRepository resolved to a local path.."""

    repo: GitRepository
    """The GitRepository object."""

    artifact: GitArtifact
    """The GitArtifact object."""

    @property
    def repo_name(self) -> str:
        """Return the name of the repository."""
        return self.repo.repo_name


@contextmanager
def empty_registry_config_file() -> Generator[Path]:
    """Context manager a temporary JSON configuration file.

    This is needed because some versions of helm can't handle reading /dev/null.
    It is preferred to call this once at the start of the program to create the
    empty json file. It may be called multiple times and it will reuse the
    existing file.
    """
    if (existing_path := _config_context.get()) is not None:
        # Reuse existing config file already created
        yield Path(existing_path)
        return

    with tempfile.NamedTemporaryFile(
        mode="w+",
        suffix=".json",
    ) as temp_file:
        temp_file_path = Path(temp_file.name)
        temp_file_path.write_text("{}")
        token = _config_context.set(str(temp_file_path))
        try:
            yield temp_file_path
        finally:
            _config_context.reset(token)


def _get_registry_config_file() -> str:
    """Get the current registry config file."""
    if (filename := _config_context.get()) is None:
        raise ValueError(
            "No registry config file found, call with empty_registry_config_file() first"
        )
    return filename


def _chart_name(
    release: HelmRelease,
    repo: HelmRepository | OCIRepository | LocalGitRepository | None,
) -> str:
    """Return the helm chart name used for the helm template command."""
    if release.chart.repo_kind == OCI_REPOSITORY:
        if not repo:
            raise HelmException(
                f"Unable to find OCIRepository for {release.chart.chart_name} for "
                f"HelmRelease {release.name}"
            )
        if isinstance(repo, OCIRepository):
            return repo.url
        raise HelmException(
            f"HelmRelease {release.name} expected OCIRepository but got HelmRepository {repo.repo_name}"
        )
    if release.chart.repo_kind == HELM_REPO_KIND:
        if not repo:
            raise HelmException(
                f"Unable to find HelmRepository for {release.chart.chart_name} for "
                f"HelmRelease {release.name}"
            )
        if not isinstance(repo, HelmRepository):
            raise HelmException(
                f"HelmRelease {release.name} expected HelmRepository but got OCIRepository {repo.repo_name}"
            )
        return repo.helm_chart_name(release.chart)
    if release.chart.repo_kind == GIT_REPOSITORY:
        if isinstance(repo, LocalGitRepository):
            return str(Path(repo.artifact.local_path) / release.chart.name)
        _LOGGER.warning(
            "Unable to find chart %s for HelmRelease %s, using %s",
            release.chart.chart_name,
            release.name,
            release.chart.name,
        )
        return release.chart.name
    raise HelmException(
        f"Unable to find chart source for chart {release.chart.chart_name} "
        f"kind {release.chart.repo_kind} for HelmRelease {release.name}"
    )


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

    skip_kinds: list[str] | None = None
    """Omit these kinds in the output."""

    kube_version: str | None = None
    """Value of the helm --kube-version flag."""

    api_versions: str | None = None
    """Value of the helm --api-versions flag."""

    registry_config: str | None = None
    """Value of the helm --registry-config flag."""

    skip_invalid_paths: bool = False
    """Skip HelmReleases with invalid local paths."""

    @property
    def base_args(self) -> list[str]:
        """Helm template CLI arguments built from the options."""
        return [
            "--registry-config",
            self.registry_config or _get_registry_config_file(),
        ]

    @property
    def template_args(self) -> list[str]:
        """Helm template CLI arguments built from the options."""
        args = self.base_args
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
        if self.skip_kinds:
            skips.extend(self.skip_kinds)
        return skips


class Helm:
    """Manages local HelmRepository state."""

    def __init__(self, tmp_dir: Path, cache_dir: Path) -> None:
        """Initialize Helm."""
        self._tmp_dir = tmp_dir
        self._repo_config_file = self._tmp_dir / "repository-config.yaml"
        self._flags = [
            "--repository-cache",
            str(cache_dir),
            "--repository-config",
            str(self._repo_config_file),
        ]
        self._repos: list[HelmRepository | OCIRepository | LocalGitRepository] = []

    def add_repo(
        self, repo: HelmRepository | OCIRepository | LocalGitRepository
    ) -> None:
        """Add the specified HelmRepository to the local config."""
        self._repos.append(repo)

    def add_repos(
        self,
        repos: (
            list[HelmRepository]
            | list[OCIRepository]
            | list[HelmRepository | OCIRepository]
            | list[HelmRepository | OCIRepository | LocalGitRepository]
        ),
    ) -> None:
        """Add the specified HelmRepository to the local config."""
        for repo in repos:
            self._repos.append(repo)

    async def update(self) -> None:
        """Run the update command to update the local repo.

        Typically the repository must be updated before doing any chart templating.
        """
        _LOGGER.debug("Updating %d repositories", len(self._repos))
        helm_repos = [
            repo
            for repo in self._repos
            if isinstance(repo, HelmRepository) and repo.repo_type != REPO_TYPE_OCI
        ]
        if not helm_repos:
            return
        content = yaml.dump(RepositoryConfig(helm_repos).config, sort_keys=False)
        async with aiofiles.open(str(self._repo_config_file), mode="w") as config_file:
            await config_file.write(content)
        with empty_registry_config_file():
            args = [HELM_BIN, "repo", "update"]
            args.extend(Options().base_args)
            args.extend(self._flags)
            await command.run(command.Command(args, exc=HelmException))

    async def is_invalid_local_path(
        self,
        release: HelmRelease,
    ) -> bool:
        """Check if the HelmRelease has an invalid GitRepository path."""
        repo = next(
            iter([repo for repo in self._repos if repo.repo_name == release.repo_name]),
            None,
        )
        chart_name = _chart_name(release, repo)
        if release.chart.repo_kind == GIT_REPOSITORY:
            if not await exists(chart_name):
                return True
        return False

    async def template(
        self,
        release: HelmRelease,
        options: Options | None = None,
    ) -> Kustomize:
        """Return command to evaluate the template of the specified chart.

        The values will come from the `HelmRelease` object.
        """
        if options is None:
            options = Options()
        repo = next(
            iter([repo for repo in self._repos if repo.repo_name == release.repo_name]),
            None,
        )
        with empty_registry_config_file():
            args: list[str] = [
                HELM_BIN,
                "template",
                release.name,
                _chart_name(release, repo),
                "--namespace",
                release.release_namespace,
            ]
            args.extend(self._flags)
            args.extend(options.template_args)
            if release.disable_openapi_validation:
                args.append("--disable-openapi-validation")
            if release.disable_schema_validation:
                args.append("--skip-schema-validation")
            if release.chart.version:
                args.extend(
                    [
                        "--version",
                        release.chart.version,
                    ]
                )
            elif isinstance(repo, OCIRepository) and (oci_version := repo.version()):
                args.extend(
                    [
                        "--version",
                        oci_version,
                    ]
                )
            if release.values:
                values_path = self._tmp_dir / f"{release.release_name}-values.yaml"
                async with aiofiles.open(values_path, mode="w") as values_file:
                    await values_file.write(yaml.dump(release.values, sort_keys=False))
                args.extend(["--values", str(values_path)])
            cmd = Kustomize([command.Command(args, exc=HelmException)])
            if options.skip_resources:
                cmd = cmd.skip_resources(options.skip_resources)
            return cmd
