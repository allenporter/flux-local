"""Library for common selectors."""

import logging
import pathlib
from argparse import ArgumentParser, BooleanOptionalAction

from flux_local import git_repo

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "flux-system"


def add_selector_flags(args: ArgumentParser) -> None:
    """Add common selector flags to the arguments object."""
    args.add_argument(
        "--path",
        help="Optional path with flux Kustomization resources (multi-cluster ok)",
        type=pathlib.Path,
        default=None,
        nargs="?",
    )
    args.add_argument(
        "--all-namespaces",
        "-A",
        type=bool,
        default=False,
        action=BooleanOptionalAction,
        help="List the requested objects across all namespaces.",
    )
    args.add_argument(
        "--namespace",
        "-n",
        type=str,
        default=DEFAULT_NAMESPACE,
        help="If present, the namespace scope for this request",
    )


def add_ks_selector_flags(args: ArgumentParser) -> None:
    """Add common kustomization selector flags to the arguments object."""
    args.add_argument(
        "kustomization",
        help="The name of the flux Kustomization",
        type=str,
        default=None,
        nargs="?",
    )
    add_selector_flags(args)


def build_ks_selector(  # type: ignore[no-untyped-def]
    **kwargs,
) -> git_repo.ResourceSelector:
    """Build a selector object form the specified flags."""
    selector = git_repo.ResourceSelector()
    selector.path = git_repo.PathSelector(kwargs.get("path"))
    selector.kustomization.name = kwargs.get("kustomization")
    selector.kustomization.namespace = kwargs.get("namespace")
    if kwargs.get("all_namespaces"):
        selector.kustomization.namespace = None
    return selector


def add_hr_selector_flags(args: ArgumentParser) -> None:
    """Add common HelmRelease selector flags to the arguments object."""
    args.add_argument(
        "helmrelease",
        help="The name of the flux Kustomization",
        type=str,
        default=None,
        nargs="?",
    )
    add_selector_flags(args)


def build_hr_selector(  # type: ignore[no-untyped-def]
    **kwargs,
) -> git_repo.ResourceSelector:
    """Build a selector object form the specified flags."""
    _LOGGER.debug("Building HelmRelease selector from args: %s", kwargs)
    selector = git_repo.ResourceSelector()
    selector.path = git_repo.PathSelector(kwargs.get("path"))
    selector.helm_release.name = kwargs.get("helmrelease")
    selector.helm_release.namespace = kwargs.get("namespace")
    if kwargs.get("all_namespaces"):
        selector.helm_release.namespace = None
    return selector


def not_found(resource: str, mds: git_repo.MetadataSelector) -> str:
    """Return a not found error message for the given resource type and query."""
    if mds.name:
        return (
            f"{resource} object '{mds.name}' not found in '{mds.namespace}' namespace"
        )
    if mds.namespace:
        return f"no {resource} objects found in '{mds.namespace}' namespace"
    return f"no {resource} objects found in cluster"
