"""Library for common selectors."""

import pathlib
from argparse import ArgumentParser, BooleanOptionalAction

from flux_local import git_repo


DEFAULT_NAMESPACE = "flux-system"


def add_selector_flags(args: ArgumentParser) -> None:
    """Add common selector flags to the arguments object."""
    args.add_argument(
        "--path",
        help="Optional path with flux Kustomization resources (multi-cluster ok)",
        type=pathlib.Path,
        default=".",
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
    kustomization = kwargs.get("kustomization")
    namespace = kwargs.get("namespace")
    if kwargs.get("all_namespaces"):
        namespace = None
    if kustomization or namespace:
        selector.kustomization = git_repo.MetadataSelector(
            name=kustomization, namespace=namespace
        )
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
    selector = git_repo.ResourceSelector()
    selector.path = git_repo.PathSelector(kwargs.get("path"))
    helmrelease = kwargs.get("helmrelease")
    namespace = kwargs.get("namespace")
    if kwargs.get("all_namespaces"):
        namespace = None
    if helmrelease or namespace:
        selector.helm_release = git_repo.MetadataSelector(
            name=helmrelease, namespace=namespace
        )
    return selector


def not_found(resource: str, mds: git_repo.MetadataSelector) -> str:
    """Return a not found error message for the given resource type and query."""
    if mds.name:
        return (
            f"{resource} object '{mds.name}' not found in '{mds.namespace}' namespace"
        )
    if mds.namespace:
        return f"no {resource} objects found in '{mds.namespace}' namespace"
    return "no {resource} objects found in cluster"
