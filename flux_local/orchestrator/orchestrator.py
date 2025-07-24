"""Orchestrator for flux-local.

This module provides the main orchestrator that coordinates the execution of
various controllers to manage the reconciliation of Flux resources.
"""

import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass, field
import tempfile
from typing import Any
import yaml

from flux_local.exceptions import FluxException
from flux_local.store import Store, Status
from flux_local.source_controller import (
    SourceController,
    SourceControllerConfig,
    GitArtifact,
)
from flux_local.kustomize_controller.controller import (
    KustomizationController,
    KustomizationControllerConfig,
)
from flux_local.helm_controller.controller import (
    HelmReleaseController,
    HelmControllerConfig,
)
from flux_local.manifest import GitRepository, Kustomization, NamedResource
from flux_local.helm import Helm
from flux_local.task import get_task_service
from flux_local import git_repo

from .loader import ResourceLoader, LoadOptions, ReadAction

_LOGGER = logging.getLogger(__name__)


BOOTSTRAP_GIT_REPO_TEMPLATE = """\
---
kind: GitRepository
apiVersion: source.toolkit.fluxcd.io/v1
metadata:
    name: {name}
    namespace: {namespace}
spec:
    interval: 1m
    url: file://{path}
    ref:
        branch: main
"""


@dataclass
class OrchestratorConfig:
    """Configuration for the orchestrator.

    Attributes:
        enable_helm: Whether to enable Helm support.
    """

    enable_helm: bool = True
    helm_controller_config: HelmControllerConfig = field(
        default_factory=HelmControllerConfig
    )
    kustomization_controller_config: KustomizationControllerConfig = field(
        default_factory=KustomizationControllerConfig
    )
    source_controller_config: SourceControllerConfig = field(
        default_factory=SourceControllerConfig
    )
    read_action_config: ReadAction = field(default_factory=ReadAction)


@dataclass
class BootstrapOptions:
    """Options for configuring the bootstrap process.

    Attributes:
        path: The path to the repository root to load resources from.
        bootstrap_repo_name: An optional name of a GitRepository to use for
            associating with the local git repository. If not specified, one
            will be inferred if there is only a single GitRepository found.
    """

    path: Path
    bootstrap_repo_name: str | None = None


class Orchestrator:
    """Orchestrator for coordinating the execution of controllers.

    The orchestrator is responsible for:
    - Managing the lifecycle of controllers
    - Coordinating the execution of different controllers
    - Ensuring proper ordering of operations
    - Providing a unified interface for starting/stopping the system
    """

    def __init__(
        self,
        store: Store,
        config: OrchestratorConfig | None = None,
    ) -> None:
        """Initialize the orchestrator."""
        self.store = store
        self.config = config or OrchestratorConfig()
        self.controllers: dict[str, Any] = {}

    def _create_controllers(self) -> None:
        """Create and initialize all controllers."""

        self.controllers = {
            "source": SourceController(
                self.store, self.config.source_controller_config
            ),
            "kustomize": KustomizationController(
                self.store, self.config.kustomization_controller_config
            ),
        }

        if self.config.enable_helm:
            helm_tmp_dir = Path(tempfile.mkdtemp(prefix="flux-helm-tmp-"))
            helm_cache_dir = Path(tempfile.mkdtemp(prefix="flux-helm-cache-"))
            self.controllers["helm"] = HelmReleaseController(
                self.store,
                Helm(tmp_dir=helm_tmp_dir, cache_dir=helm_cache_dir),
                self.config.helm_controller_config,
            )

        _LOGGER.debug("Initialized controllers: %s", ", ".join(self.controllers.keys()))

    async def start(self) -> None:
        """Start the orchestrator and all controllers."""
        if self.controllers:
            return

        _LOGGER.info("Starting orchestrator")
        self._create_controllers()

    async def stop(self) -> None:
        """Stop the orchestrator and all controllers."""
        if not self.controllers:
            return

        _LOGGER.info("Stopping orchestrator")

        # Stop controllers in reverse order
        for name, controller in reversed(self.controllers.items()):
            _LOGGER.debug("Stopping controller: %s", name)
            await controller.close()

        # Wait for all tasks to complete
        await get_task_service().block_till_done()
        self.controllers.clear()
        _LOGGER.info("Orchestrator stopped")

    def has_failed_resources(self) -> bool:
        """Check if any resources have failed.

        Returns:
            bool: True if any resources have failed, False otherwise.
        """
        return self.store.has_failed_resources()

    def is_complete(self) -> bool:
        """Check if all work is complete.

        Returns:
            bool: True if all tasks are done and no resources have failed.
        """
        # Check if there are any active tasks
        task_service = get_task_service()
        if task_service.get_num_active_tasks() > 0:
            return False

        # Check for any failed resources
        if self.has_failed_resources():
            _LOGGER.error("One or more resources have failed")
            return True

        # If we get here, all tasks are done and no resources have failed
        return True

    def _find_or_create_bootstrap_repo(
        self,
        git_repos: list[GitRepository],
        options: BootstrapOptions,
        repo_root: Path,
    ) -> GitRepository:
        """Find the bootstrap GitRepository or create a synthetic one."""
        if options.bootstrap_repo_name:
            for repo in git_repos:
                if repo.name == options.bootstrap_repo_name:
                    _LOGGER.info(
                        "Found bootstrap GitRepository specified by name: %s",
                        repo.repo_name,
                    )
                    return repo
            raise FluxException(
                f"Bootstrap GitRepository '{options.bootstrap_repo_name}' not found"
            )

        if len(git_repos) == 1:
            _LOGGER.info(
                "Found single bootstrap GitRepository: %s", git_repos[0].repo_name
            )
            return git_repos[0]

        if len(git_repos) > 1:
            names = [repo.name for repo in git_repos]
            raise FluxException(
                "Multiple GitRepository objects found, specify one with "
                f"'bootstrap_repo_name' option: {names}"
            )

        _LOGGER.info("No GitRepository found; creating a synthetic one")
        return GitRepository.parse_doc(
            yaml.load(
                BOOTSTRAP_GIT_REPO_TEMPLATE.format(
                    name="flux-system",
                    namespace="flux-system",
                    path=str(repo_root),
                ),
                Loader=yaml.SafeLoader,
            )
        )

    async def bootstrap(self, options: BootstrapOptions) -> bool:
        """Bootstrap the system by loading resources and starting controllers.

        This is a convenience method that loads resources and starts the orchestrator.

        Args:
            options: The bootstrap options for finding resources.

        Returns:
            bool: True if bootstrap was successful, False otherwise
        """

        _LOGGER.info("Starting bootstrap from path: %s", options.path)

        # 1. Load initial resources
        loader = ResourceLoader(self.config.read_action_config)
        git_repos: list[GitRepository] = []
        kustomizations: list[Kustomization] = []
        try:
            async for resource in loader.load(
                LoadOptions(path=options.path, recursive=True)
            ):
                if isinstance(resource, GitRepository):
                    git_repos.append(resource)
                elif isinstance(resource, Kustomization):
                    kustomizations.append(resource)
        except FluxException as e:
            _LOGGER.error("Failed to load initial resources: %s", e, exc_info=True)
            return False
        except Exception as e:
            _LOGGER.error("Failed to load initial resources: %s", e, exc_info=True)
            return False

        # 2. Find the bootstrap GitRepository to associate with the local path
        repo = git_repo.git_repo(options.path)
        repo_root = git_repo.repo_root(repo)
        abs_root = repo_root.expanduser().resolve()

        bootstrap_repo = self._find_or_create_bootstrap_repo(
            git_repos, options, repo_root
        )
        repo_id = NamedResource(
            kind="GitRepository",
            namespace=bootstrap_repo.namespace,
            name=bootstrap_repo.name,
        )
        self.store.add_object(bootstrap_repo)
        self.store.set_artifact(
            repo_id,
            GitArtifact(
                url="",
                local_path=str(abs_root),
            ),
        )
        self.store.update_status(
            repo_id,
            Status.READY,
        )
        _LOGGER.info("Added bootstrap GitRepository: %s", repo_id)

        # Add Kustomization resources to the store
        for resource in kustomizations:
            self.store.add_object(resource)

        # 3. Start controllers and run
        await self.start()
        try:
            return await self.run()
        finally:
            await self.stop()

    async def run(self) -> bool:
        """Run the orchestrator until all work is complete.

        Returns:
            bool: True if all work completed successfully, False if any resources failed.
        """
        try:
            await self.start()

            # Wait for completion or error
            while True:
                if self.has_failed_resources():
                    _LOGGER.error("Resource failures detected, stopping")
                    return False

                if self.is_complete():
                    _LOGGER.info("All work completed successfully")
                    return True

                # Allow tasks to run and avoid busy waiting
                await get_task_service().block_till_done()
                await asyncio.sleep(0.001)

        except asyncio.CancelledError:
            _LOGGER.info("Orchestrator was cancelled")
            return False

        except Exception as e:
            _LOGGER.exception("Orchestrator failed: %s", e)
            return False

        finally:
            await self.stop()
