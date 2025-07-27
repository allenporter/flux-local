"""Library for common selectors."""

from argparse import (
    ArgumentParser,
    BooleanOptionalAction,
    Action,
    ArgumentError,
    Namespace,
)
import logging
import pathlib
import shlex
from typing import Any

from flux_local import git_repo, helm

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "flux-system"


class SourceAppendAction(Action):
    """Append a key=value pair to the argument list."""

    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        values = values.split(",")
        if not values[0]:
            return
        result = getattr(namespace, self.dest) or []
        for value in values:
            try:
                source = git_repo.Source.from_str(value)
            except ValueError:
                raise ArgumentError(
                    self, f"Expected key or key=value format from '{value}'"
                )
            result.append(source)
        setattr(namespace, self.dest, result)


class SelectorAppendAction(Action):
    """Append a key=value pair to the argument dict."""

    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        values = values.split(",")
        if not values[0]:
            return
        result = getattr(namespace, self.dest) or {}
        for value in values:
            if "=" not in value:
                raise ValueError(f"Expected key=value format but got '{value}'")
            k, v = value.split("=")
            result[k] = v
        setattr(namespace, self.dest, result)


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
        "--sources",
        help="Optional GitRepository or OCIRepository sources to restrict "
        "to e.g. `flux-system`. Can include optional map of repository "
        "source to relative path e.g. `cluster=./k8s/`",
        action=SourceAppendAction,
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
    args.add_argument(
        "--label-selector",
        "-l",
        action=SelectorAppendAction,
        help="Filter objects by label selector by name=value",
    )
    add_common_flags(args)


def add_common_flags(args: ArgumentParser) -> None:
    """Add flags that are common to selectors and other command types."""
    args.add_argument(
        "--skip-crds",
        type=str,
        default=True,
        action=BooleanOptionalAction,
        help="When true do not include CRDs to reduce output size",
    )
    args.add_argument(
        "--skip-secrets",
        type=str,
        default=True,
        action=BooleanOptionalAction,
        help="When true do not include Secrets to reduce output size and randomness",
    )
    args.add_argument(
        "--skip-kinds",
        type=lambda x: x.split(","),
        help="A comma separated list of CRDs to omit from the output.",
    )
    args.add_argument(
        "--skip-invalid-kustomization-paths",
        default=False,
        action=BooleanOptionalAction,
        help="Don't validate kustomization paths exist.",
    )
    args.add_argument(
        "--kustomize-build-flags",
        type=str,
        default="",
        help="If present, additional flags to pass to `kustomize build`",
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


def options(  # type: ignore[no-untyped-def]
    kustomize_build_flags: str | None, **kwargs
) -> git_repo.Options:
    """Create an Options object based on flags."""
    options = git_repo.Options()
    options.kustomize_flags = shlex.split(kustomize_build_flags or "")
    options.skip_kustomize_path_validation = kwargs.get(
        "skip_invalid_kustomization_paths", False
    )
    return options


def build_ks_selector(  # type: ignore[no-untyped-def]
    **kwargs,
) -> git_repo.ResourceSelector:
    """Build a selector object form the specified flags."""
    selector = git_repo.ResourceSelector()
    selector.path = git_repo.PathSelector(
        kwargs.get("path"), sources=kwargs.get("sources")
    )
    selector.kustomization.name = kwargs["kustomization"]
    selector.kustomization.namespace = kwargs["namespace"]
    if kwargs["all_namespaces"]:
        selector.kustomization.namespace = None
    selector.kustomization.label_selector = kwargs["label_selector"]
    selector.kustomization.skip_crds = kwargs["skip_crds"]
    selector.kustomization.skip_secrets = kwargs["skip_secrets"]
    selector.kustomization.skip_kinds = kwargs["skip_kinds"]
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
    selector.path = git_repo.PathSelector(
        kwargs.get("path"), sources=kwargs.get("sources")
    )
    selector.helm_release.name = kwargs.get("helmrelease")
    selector.helm_release.namespace = kwargs["namespace"]
    if kwargs["all_namespaces"]:
        selector.helm_release.namespace = None
    selector.helm_release.label_selector = kwargs["label_selector"]
    selector.helm_release.skip_crds = kwargs["skip_crds"]
    selector.helm_release.skip_secrets = kwargs["skip_secrets"]
    selector.helm_release.skip_kinds = kwargs["skip_kinds"]
    selector.kustomization.name = None
    selector.kustomization.namespace = None
    return selector


def add_helm_options_flags(args: ArgumentParser) -> None:
    """Add common helm template options flags to the arguments object."""
    args.add_argument(
        "--kube-version",
        help="Kubernetes version used for Capabilities.KubeVersion",
    )
    args.add_argument(
        "--api-versions",
        "-a",
        help="Kubernetes api versions used for helm Capabilities.APIVersions",
    )
    args.add_argument(
        "--registry-config",
        help="Path to a helm registry config file",
    )
    args.add_argument(
        "--skip-invalid-helm-release-paths",
        type=bool,
        default=True,
        action=BooleanOptionalAction,
        help="When true, skip helm releases with local paths that do not exist",
    )


def build_helm_options(**kwargs) -> helm.Options:  # type: ignore[no-untyped-def]
    """Build a helm Options object from the flags.

    This assumes that the hr selector and helm options flags methods were
    called to add arguments to the parser.
    """
    return helm.Options(
        skip_crds=kwargs["skip_crds"],
        skip_secrets=kwargs["skip_secrets"],
        skip_kinds=kwargs["skip_kinds"],
        kube_version=kwargs.get("kube_version"),
        api_versions=kwargs.get("api_versions"),
        registry_config=kwargs.get("registry_config"),
        skip_invalid_paths=kwargs.get("skip_invalid_helm_release_paths", False),
    )


def add_cluster_selector_flags(args: ArgumentParser) -> None:
    """Add common flux cluster selector flags to the arguments object."""
    add_selector_flags(args)


def build_cluster_selector(  # type: ignore[no-untyped-def]
    **kwargs,
) -> git_repo.ResourceSelector:
    """Build a selector object form the specified flags."""
    _LOGGER.debug("Building flux cluster Kustomization selector from args: %s", kwargs)
    selector = git_repo.ResourceSelector()
    selector.path = git_repo.PathSelector(
        kwargs.get("path"), sources=kwargs.get("sources")
    )
    selector.kustomization.namespace = kwargs.get("namespace")
    if kwargs.get("all_namespaces"):
        selector.cluster.namespace = None
        selector.kustomization.namespace = None
    selector.kustomization.label_selector = kwargs["label_selector"]
    selector.kustomization.skip_crds = kwargs["skip_crds"]
    selector.kustomization.skip_secrets = kwargs["skip_secrets"]
    selector.kustomization.skip_kinds = kwargs["skip_kinds"]
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
