"""Flux local test action."""

from argparse import ArgumentParser, BooleanOptionalAction
from argparse import _SubParsersAction as SubParsersAction
import asyncio
import logging
import pathlib
import tempfile
from pathlib import Path
from typing import cast, Generator, Any

import nest_asyncio
import pytest

from flux_local import git_repo, kustomize
from flux_local.helm import Helm
from flux_local.manifest import (
    Manifest,
    Cluster,
    Kustomization,
    HelmRelease,
    HelmRepository,
)
from . import selector

_LOGGER = logging.getLogger(__name__)


class HelmReleaseTest(pytest.Item):
    """Test case for a Kustomization."""

    cluster: Cluster
    kustomization: Kustomization
    helm_release: HelmRelease

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        cluster: Cluster,
        kustomization: Kustomization,
        helm_release: HelmRelease,
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
            cmd = await helm.template(self.helm_release, skip_crds=True)
            await cmd.objects()

    def active_repos(self) -> list[HelmRepository]:
        """Return HelpRepositories referenced by a HelmRelease."""
        repo_key = "-".join(
            [
                self.helm_release.chart.repo_namespace,
                self.helm_release.chart.repo_name,
            ]
        )
        return [repo for repo in self.cluster.helm_repos if repo.repo_name == repo_key]


class KustomizationTest(pytest.Item):
    """Test case for a Kustomization."""

    cluster: Cluster
    kustomization: Kustomization

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        cluster: Cluster,
        kustomization: Kustomization,
        **kw: Any,
    ) -> "KustomizationTest":
        """The public constructor."""
        item: KustomizationTest = super().from_parent(
            parent=parent, path=Path(kustomization.path), name="kustomization", **kw
        )
        item.cluster = cluster
        item.kustomization = kustomization
        return item

    def runtest(self) -> None:
        """Dispatch the async work and run the test."""
        nest_asyncio.apply()
        asyncio.run(self.async_runtest())

    async def async_runtest(self) -> None:
        """Run the Kustomizations test."""
        await kustomize.build(Path(self.kustomization.path)).objects()


class KustomizationCollector(pytest.Collector):
    """Test collector for a Kustomization."""

    cluster: Cluster
    kustomization: Kustomization

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        cluster: Cluster,
        kustomization: Kustomization,
        **kw: Any,
    ) -> "KustomizationCollector":
        """The public constructor."""
        item: KustomizationCollector = super().from_parent(
            parent=parent, name=kustomization.name, path=Path(kustomization.path), **kw
        )
        item.cluster = cluster
        item.kustomization = kustomization
        return item

    def collect(self) -> Generator[pytest.Item | pytest.Collector, None, None]:
        """Collect tests from the cluster manifest.yaml file."""
        yield KustomizationTest.from_parent(
            parent=self,
            cluster=self.cluster,
            kustomization=self.kustomization,
        )
        for helm_release in self.kustomization.helm_releases:
            yield HelmReleaseTest.from_parent(
                parent=self,
                cluster=self.cluster,
                kustomization=self.kustomization,
                helm_release=helm_release,
            )


class ClusterCollector(pytest.Collector):
    """Test collector for a Cluster."""

    cluster: Cluster

    @classmethod
    def from_parent(  # type: ignore[override]
        cls, parent: Any, *, cluster: Cluster, **kw: Any
    ) -> "ClusterCollector":
        """The public constructor."""
        item: ClusterCollector = super().from_parent(
            parent=parent,
            name=cluster.name,
            path=Path(cluster.path),
            nodeid=cluster.path,
        )
        item.cluster = cluster
        return item

    def collect(self) -> Generator[pytest.Item | pytest.Collector, None, None]:
        """Collect tests from the cluster."""
        for kustomization in self.cluster.kustomizations:
            yield KustomizationCollector.from_parent(
                parent=self,
                cluster=self.cluster,
                kustomization=kustomization,
            )


class ManifestCollector(pytest.Collector):
    """Test collector for a Kustomization."""

    manifest: Manifest

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: Any,
        *,
        manifest: Manifest,
        **kw: Any,
    ) -> "ManifestCollector":
        """The public constructor."""
        item: ManifestCollector = super().from_parent(
            parent=parent, name="manifest", **kw
        )
        item.manifest = manifest
        return item

    def collect(self) -> Generator[pytest.Item | pytest.Collector, None, None]:
        """Collect tests from the cluster manifest."""
        for cluster in self.manifest.clusters:
            yield ClusterCollector.from_parent(
                parent=self,
                cluster=cluster,
            )


class ManifestPlugin:
    """Loads the flux-local Manifest based on command line arguments.

    This plugin prepares the `test_kustomization` plugin with the manifest. That plugin
    is loaded separately to avoid collection.
    """

    def __init__(self, selector: git_repo.ResourceSelector) -> None:
        self.selector = selector
        self.manifest: Manifest | None = None

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        nest_asyncio.apply()
        asyncio.run(self.async_pytest_sessionstart(session))

    async def async_pytest_sessionstart(self, session: pytest.Session) -> None:
        """Run the Kustomizations test."""
        manifest = await git_repo.build_manifest(selector=self.selector)
        self.manifest = manifest

    def pytest_collection(self, session: pytest.Session) -> None:
        _LOGGER.debug("pytest_collection:%s", session)
        if not self.manifest:
            raise ValueError("ManifestPlugin not initialized properly")
        manifest_collector = ManifestCollector.from_parent(
            parent=session, manifest=self.manifest
        )
        # Ignore the default files found by pytest and instead create
        # tests based on the manifest contents.
        session.collect = manifest_collector.collect  # type: ignore[assignment]


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
            help="Optional path with flux Kustomization resources (multi-cluster ok)",
            type=pathlib.Path,
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
        args.set_defaults(cls=cls, verbosity=0)
        selector.add_cluster_selector_flags(args)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        enable_helm: bool,
        test_path: Path,
        verbosity: int,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        query = selector.build_cluster_selector(**kwargs)
        if test_path:
            query.path.path = test_path
        query.kustomization.skip_crds = True
        query.helm_release.enabled = enable_helm
        query.helm_release.namespace = None

        nest_asyncio.apply()
        pytest_args = [
            "--verbosity",
            str(verbosity),
            "--no-header",
            "--disable-warnings",
            # Disable plugins used by this library that generates warnings
            "-p",
            "no:pytest-golden",
        ]
        _LOGGER.debug("pytest.main: %s", pytest_args)
        pytest.main(
            pytest_args,
            plugins=[ManifestPlugin(query)],
        )
