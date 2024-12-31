"""Flux local test action."""

from argparse import (
    ArgumentParser,
    BooleanOptionalAction,
    _SubParsersAction as SubParsersAction,
)
import asyncio
from dataclasses import dataclass
import logging
import pathlib
import tempfile
from pathlib import Path
import sys
from typing import cast, Generator, Any

import nest_asyncio
import pytest

from flux_local import git_repo, kustomize
from flux_local.exceptions import FluxException
from flux_local.helm import Helm, Options
from flux_local.manifest import (
    Manifest,
    Cluster,
    Kustomization,
    HelmRelease,
    HelmRepository,
    OCIRepository,
)
from . import selector

_LOGGER = logging.getLogger(__name__)


@dataclass
class TestConfig:
    """Test configuration, which are parameters to types of the tests."""

    options: git_repo.Options
    helm_options: Options


class HelmReleaseTest(pytest.Item):
    """Test case for a Kustomization."""

    cluster: Cluster
    kustomization: Kustomization
    helm_release: HelmRelease
    test_config: TestConfig

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        cluster: Cluster,
        kustomization: Kustomization,
        helm_release: HelmRelease,
        test_config: TestConfig,
        **kw: Any,
    ) -> "HelmReleaseTest":
        """The public constructor."""
        item: HelmReleaseTest = super().from_parent(
            parent=parent,
            path=Path(kustomization.path),
            name=f"{helm_release.namespace}/{helm_release.name}",
            **kw,
        )
        item.cluster = cluster
        item.kustomization = kustomization
        item.helm_release = helm_release
        item.test_config = test_config
        return item

    def runtest(self) -> None:
        """Dispatch the async work and run the test."""
        nest_asyncio.apply()
        asyncio.run(self.async_runtest())

    async def async_runtest(self) -> None:
        """Run the Kustomizations test."""
        # Note: This could be sped up by sharing a cache dir across clusters for the
        # multi-cluster git repos.
        with (
            tempfile.TemporaryDirectory() as helm_cache_dir,
            tempfile.TemporaryDirectory() as tmp_dir,
        ):
            helm = Helm(pathlib.Path(tmp_dir), pathlib.Path(helm_cache_dir))
            helm.add_repos(self.active_repos())
            await helm.update()
            cmd = await helm.template(
                self.helm_release,
                self.test_config.helm_options,
            )
            await cmd.objects()

    def active_repos(self) -> list[HelmRepository | OCIRepository]:
        """Return HelmRepositories referenced by a HelmRelease."""
        repo_key = "-".join(
            [
                self.helm_release.chart.repo_namespace,
                self.helm_release.chart.repo_name,
            ]
        )
        return [
            repo
            for repo in self.cluster.helm_repos + self.cluster.oci_repos
            if repo.repo_name == repo_key
        ]


class KustomizationTest(pytest.Item):
    """Test case for a Kustomization."""

    cluster: Cluster
    kustomization: Kustomization
    test_config: TestConfig

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        cluster: Cluster,
        kustomization: Kustomization,
        test_config: TestConfig,
        **kw: Any,
    ) -> "KustomizationTest":
        """The public constructor."""
        item: KustomizationTest = super().from_parent(
            parent=parent, path=Path(kustomization.path), name="kustomization", **kw
        )
        item.cluster = cluster
        item.kustomization = kustomization
        item.test_config = test_config
        return item

    def runtest(self) -> None:
        """Dispatch the async work and run the test."""
        nest_asyncio.apply()
        asyncio.run(self.async_runtest())

    async def async_runtest(self) -> None:
        """Run the Kustomizations test."""
        cmd = await kustomize.flux_build(
            self.kustomization, Path(self.kustomization.path)
        ).stash()
        await cmd.objects()


class KustomizationCollector(pytest.Collector):
    """Test collector for a Kustomization."""

    cluster: Cluster
    kustomization: Kustomization
    test_config: TestConfig

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        cluster: Cluster,
        kustomization: Kustomization,
        test_config: TestConfig,
        **kw: Any,
    ) -> "KustomizationCollector":
        """The public constructor."""
        item: KustomizationCollector = super().from_parent(
            parent=parent, name=kustomization.name, path=Path(kustomization.path), **kw
        )
        item.cluster = cluster
        item.kustomization = kustomization
        item.test_config = test_config
        return item

    def collect(self) -> Generator[pytest.Item | pytest.Collector, None, None]:
        """Collect tests from the cluster manifest.yaml file."""
        yield KustomizationTest.from_parent(
            parent=self,
            cluster=self.cluster,
            kustomization=self.kustomization,
            test_config=self.test_config,
        )
        for helm_release in self.kustomization.helm_releases:
            yield HelmReleaseTest.from_parent(
                parent=self,
                cluster=self.cluster,
                kustomization=self.kustomization,
                helm_release=helm_release,
                test_config=self.test_config,
            )


class ClusterCollector(pytest.Collector):
    """Test collector for a Cluster."""

    cluster: Cluster
    test_config: TestConfig

    @classmethod
    def from_parent(  # type: ignore[override]
        cls, parent: Any, *, cluster: Cluster, test_config: TestConfig, **kw: Any
    ) -> "ClusterCollector":
        """The public constructor."""
        item: ClusterCollector = super().from_parent(
            parent=parent,
            name=cluster.path,
            path=Path(cluster.path),
            nodeid=str(Path(cluster.path)),
        )
        item.cluster = cluster
        item.test_config = test_config
        return item

    def collect(self) -> Generator[pytest.Item | pytest.Collector, None, None]:
        """Collect tests from the cluster."""
        for kustomization in self.cluster.kustomizations:
            yield KustomizationCollector.from_parent(
                parent=self,
                cluster=self.cluster,
                kustomization=kustomization,
                test_config=self.test_config,
            )


class ManifestCollector(pytest.Collector):
    """Test collector for a Kustomization."""

    manifest: Manifest
    test_config: TestConfig

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        manifest: Manifest,
        test_config: TestConfig,
        **kw: Any,
    ) -> "ManifestCollector":
        """The public constructor."""
        item: ManifestCollector = super().from_parent(
            parent=parent, name="manifest", **kw
        )
        item.manifest = manifest
        item.test_config = test_config
        return item

    def collect(self) -> Generator[pytest.Item | pytest.Collector, None, None]:
        """Collect tests from the cluster manifest."""
        for cluster in self.manifest.clusters:
            yield ClusterCollector.from_parent(
                parent=self,
                cluster=cluster,
                test_config=self.test_config,
            )


class ManifestPlugin:
    """Loads the flux-local Manifest based on command line arguments.

    This plugin prepares the `test_kustomization` plugin with the manifest. That plugin
    is loaded separately to avoid collection.
    """

    def __init__(
        self,
        selector: git_repo.ResourceSelector,
        test_config: TestConfig,
        test_filter: list[str],
    ) -> None:
        self.selector = selector
        self.manifest: Manifest | None = None
        self.test_config = test_config
        self.test_filter = test_filter
        self.init_error: Exception | None = None

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        nest_asyncio.apply()
        asyncio.run(self.async_pytest_sessionstart(session))

    async def async_pytest_sessionstart(self, session: pytest.Session) -> None:
        """Run the Kustomizations test."""
        _LOGGER.debug("async_pytest_sessionstart")
        try:
            manifest = await git_repo.build_manifest(
                selector=self.selector,
                options=self.test_config.options,
            )
        except FluxException as err:
            _LOGGER.error("Failed to build manifest: %s", err)
            self.init_error = err
            return

        self.manifest = manifest
        _LOGGER.debug("async_pytest_sessionstart ended")

    def pytest_collection(self, session: pytest.Session) -> None:
        _LOGGER.debug("pytest_collection:%s", session)
        if self.init_error or self.manifest is None:
            raise pytest.UsageError(
                self.init_error or "ManifestPlugin not initialized properly"
            ) from self.init_error
        manifest_collector = ManifestCollector.from_parent(
            parent=session,
            manifest=self.manifest,
            test_config=self.test_config,
        )
        # Ignore the default files found by pytest and instead create
        # tests based on the manifest contents.
        session.collect = manifest_collector.collect  # type: ignore[method-assign]
        _LOGGER.debug("pytest_collection end:%s", session)

    def pytest_collection_modifyitems(
        self,
        session: pytest.Session,
        config: pytest.Config,
        items: list[pytest.Item],
    ) -> None:
        _LOGGER.debug("pytest_collection_modifyitems collected: %s", len(items))
        if self.test_filter:
            _LOGGER.debug("Filtering tests: %s", self.test_filter)
            filtered_items = []
            for item in items:
                for nodeid in self.test_filter:
                    if item.nodeid.startswith(nodeid):
                        filtered_items.append(item)
                        continue
            items.clear()
            items.extend(filtered_items)
            _LOGGER.debug("Remaining tests after collection filter: %s", len(items))


class TestAction:
    """Flux-local test action."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "test",
                help="Build and validate the cluster",
                description="""You can verify that the resources in the cluster
                    are formatted properly before commit or as part of a CI
                    system. The flux-local test command will build the
                    Kustomization resources in the cluster.""",
            ),
        )
        args.add_argument(
            "--enable-helm",
            type=bool,
            action=BooleanOptionalAction,
            help="Enable use of HelmRelease inflation",
        )
        # Flags consistent with pytest for pass through
        args.add_argument(
            "test_path",
            help="Optional path with flux Kustomization resources or full test node",
            type=str,
            default=None,
            nargs="?",
        )
        verbosity_group = args.add_mutually_exclusive_group()
        verbosity_group.add_argument(
            "--verbose",
            "-v",
            action="count",
            dest="verbosity",
            help="Increase verbosity.",
        )
        verbosity_group.add_argument(
            "--verbosity",
            action="store",
            type=int,
            metavar="N",
            dest="verbosity",
            help="Set verbosity. Default is 0",
        )
        selector.add_helm_options_flags(args)
        args.set_defaults(cls=cls, verbosity=0)
        selector.add_cluster_selector_flags(args)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        enable_helm: bool,
        test_path: str | None,
        verbosity: int,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_cluster_selector(**kwargs)
        if test_path:
            parts = test_path.split("::")
            query.path.path = Path(parts[0])

            # If a real file path, then clear so it is not a test nodeid filter
            if test_path.startswith(".") or test_path.startswith("/"):
                test_path = None
        query.kustomization.namespace = query.cluster.namespace
        query.kustomization.skip_crds = True
        query.helm_release.enabled = enable_helm
        query.helm_release.namespace = None
        options = selector.options(**kwargs)
        helm_options = selector.build_helm_options(**kwargs)

        nest_asyncio.apply()
        pytest_args = [
            "--verbosity",
            str(verbosity),
            "--no-header",
            "--disable-warnings",
        ]
        _LOGGER.debug("pytest.main: %s", pytest_args)
        retcode = pytest.main(
            pytest_args,
            plugins=[
                ManifestPlugin(
                    query,
                    TestConfig(
                        options=options,
                        helm_options=helm_options,
                    ),
                    test_filter=[str(test_path)] if test_path else [],
                )
            ],
        )
        if retcode:
            sys.exit(retcode)
