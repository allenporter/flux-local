"""Module for computing resource diffs.

This is used internally, primarily by the diff tool.
"""

from collections.abc import Iterable, Callable
from dataclasses import asdict
import difflib
import logging
import pathlib
import tempfile
from typing import Generator, Any, AsyncGenerator, TypeVar
import yaml
import json


from . import command
from .visitor import ObjectOutput, ResourceKey, HelmVisitor
from .manifest import HelmRelease, NamedResource

_LOGGER = logging.getLogger(__name__)

_TRUNCATE = "[Diff truncated by flux-local]"

T = TypeVar("T")


def _unique_keys(k1: dict[T, Any], k2: dict[T, Any]) -> Iterable[T]:
    """Return an ordered set."""
    return {
        **{k: True for k in k1.keys()},
        **{k: True for k in k2.keys()},
    }.keys()


def perform_object_diff(
    a: ObjectOutput, b: ObjectOutput, n: int, limit_bytes: int
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""
    for kustomization_key in _unique_keys(a.content, b.content):
        _LOGGER.debug(
            "Diffing results for Kustomization %s (n=%d)", kustomization_key, n
        )
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in _unique_keys(a_resources, b_resources):
            diff_text = difflib.unified_diff(
                a=a_resources.get(resource_key, []),
                b=b_resources.get(resource_key, []),
                fromfile=f"{kustomization_key.label} {resource_key.compact_label}",
                tofile=f"{kustomization_key.label} {resource_key.compact_label}",
                n=n,
            )
            size = 0
            for line in diff_text:
                size += len(line)
                if limit_bytes and size > limit_bytes:
                    yield _TRUNCATE
                    break
                yield line


async def perform_external_diff(
    cmd: list[str],
    a: ObjectOutput,
    b: ObjectOutput,
    limit_bytes: int,
) -> AsyncGenerator[str, None]:
    """Generate diffs between the two output objects."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for kustomization_key in _unique_keys(a.content, b.content):
            _LOGGER.debug(
                "Diffing results for Kustomization %s",
                kustomization_key,
            )
            a_resources = a.content.get(kustomization_key, {})
            b_resources = b.content.get(kustomization_key, {})
            keys = _unique_keys(a_resources, b_resources)

            a_file = pathlib.Path(tmpdir) / "a.yaml"
            a_file.write_text(
                "\n".join(
                    [
                        "\n".join(a_resources.get(resource_key, []))
                        for resource_key in keys
                    ]
                )
            )
            b_file = pathlib.Path(tmpdir) / "b.yaml"
            b_file.write_text(
                "\n".join(
                    [
                        "\n".join(b_resources.get(resource_key, []))
                        for resource_key in keys
                    ]
                )
            )

            out = await command.Command(
                cmd + [str(a_file), str(b_file)], retcodes=[0, 1]
            ).run()
            if out:
                result = out.decode("utf-8")
                if limit_bytes and len(result) > limit_bytes:
                    result = result[:limit_bytes] + "\n" + _TRUNCATE
                yield result


def _omit_none(obj: Any) -> dict[str, Any]:
    """Creates a dictionary with None values missing."""
    return {k: v for k, v in obj if v is not None}


def perform_yaml_diff(
    a: ObjectOutput,
    b: ObjectOutput,
    n: int,
    limit_bytes: int,
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""

    def diff_func(diffs: list[dict[str, Any]]) -> str:
        return yaml.dump(
            diffs, sort_keys=False, explicit_start=True, default_style=None
        )

    for result in _perform_function_diff(a, b, n, limit_bytes, diff_func):
        yield result


def perform_json_diff(
    a: ObjectOutput,
    b: ObjectOutput,
    n: int,
    limit_bytes: int,
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""

    def diff_func(diffs: list[dict[str, Any]]) -> str:
        return json.dumps(diffs, sort_keys=False, indent=4)

    for result in _perform_function_diff(a, b, n, limit_bytes, diff_func):
        yield result


def _perform_function_diff(
    a: ObjectOutput,
    b: ObjectOutput,
    n: int,
    limit_bytes: int,
    diff_func: Callable[[list[dict[str, Any]]], str],
) -> Generator[str, None, None]:
    """Generate diffs between the two output objects."""

    diffs: list[dict[str, Any]] = []
    for kustomization_key in _unique_keys(a.content, b.content):
        _LOGGER.debug("Diffing results for %s (n=%d)", kustomization_key, n)
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        resource_diffs = []
        for resource_key in _unique_keys(a_resources, b_resources):
            diff_text = difflib.unified_diff(
                a=a_resources.get(resource_key, []),
                b=b_resources.get(resource_key, []),
                fromfile=f"{kustomization_key.label} {resource_key.compact_label}",
                tofile=f"{kustomization_key.label} {resource_key.compact_label}",
                n=n,
            )
            diff_content = "\n".join(diff_text)
            if not diff_content:
                continue
            if limit_bytes and len(diff_content) > limit_bytes:
                diff_content = diff_content[:limit_bytes] + "\n" + _TRUNCATE
            obj = {
                **asdict(resource_key, dict_factory=_omit_none),
                "diff": diff_content,
            }
            resource_diffs.append(obj)
        if resource_diffs:
            diffs.append(
                {
                    **asdict(kustomization_key),
                    "diffs": resource_diffs,
                }
            )
    if diffs:
        yield diff_func(diffs)


def merge_helm_releases(
    a: list[HelmRelease], b: list[HelmRelease]
) -> Generator[tuple[HelmRelease | None, HelmRelease | None], None, None]:
    """Return HelmReleases joined by name."""
    a_dict = {r.release_name: r for r in a}
    b_dict = {r.release_name: r for r in b}
    for name in _unique_keys(a_dict, b_dict):
        yield a_dict.get(name), b_dict.get(name)


def merge_named_resources(
    a: list[NamedResource], b: list[NamedResource]
) -> Generator[NamedResource, None, None]:
    """Return HelmReleases joined by name."""
    a_dict = {f"{r.kind}-{r.namespaced_name}": r for r in a}
    b_dict = {f"{r.kind}-{r.namespaced_name}": r for r in b}
    for name in _unique_keys(a_dict, b_dict):
        # Emit either the left or right since they should be identical
        value: NamedResource = a_dict.get(name) or b_dict.get(name)  # type: ignore[assignment]
        yield value


def build_helm_dependency_map(
    a_visitor: HelmVisitor, b_visitor: HelmVisitor
) -> dict[NamedResource, list[NamedResource]]:
    """Return a map of all resources to the list of HelmReleases that depends on them.

    This is a mapping of all the named resources. If any of them changed, then we
    need to diff the HelmRelease to look for changes in the rendered output.
    """
    results: dict[NamedResource, list[NamedResource]] = {}
    for helmrelease_a, helmrelease_b in merge_helm_releases(
        a_visitor.releases, b_visitor.releases
    ):
        resources_a = helmrelease_a.resource_dependencies if helmrelease_a else []
        resources_b = helmrelease_b.resource_dependencies if helmrelease_b else []
        hr: HelmRelease = helmrelease_a or helmrelease_b  # type: ignore[assignment]
        hr_named_resource = NamedResource(
            kind="HelmRelease",
            namespace=hr.namespace,
            name=hr.name,
        )
        for named_resource in merge_named_resources(resources_a, resources_b):
            results[named_resource] = results.get(named_resource, []) + [
                hr_named_resource
            ]

    return results


def get_helm_release_diff_keys(
    a: ObjectOutput,
    b: ObjectOutput,
    dependency_map: dict[NamedResource, list[NamedResource]],
) -> list[ResourceKey]:
    """Return HelmRelease resource keys with diffs, by cluster."""

    # Pass #1: Check all named resources that are upstream dependencies of a
    # HelmRelease and have a content change. Save the HelmRelease name.
    hrs_to_check: set[NamedResource] = set()
    _LOGGER.debug("Checking HelmRelease dependencies")
    for kustomization_key in _unique_keys(a.content, b.content):
        _LOGGER.debug("Diffing results for Kustomization %s", kustomization_key)
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in _unique_keys(a_resources, b_resources):
            if (
                hr_named_resources := dependency_map.get(resource_key.named_resource)
            ) is None:
                continue
            if a_resources.get(resource_key) != b_resources.get(resource_key):
                _LOGGER.info(
                    "HelmReleases [%s] detected potential change in resource %s (Kustomization %s)",
                    [hr.namespaced_name for hr in hr_named_resources],
                    resource_key.namespaced_name,
                    kustomization_key.namespaced_name,
                )
                hrs_to_check.update(set(hr_named_resources))

    # Pass #2: Find the ResourceKey of all the changed HelmReleases
    results: list[ResourceKey] = []
    for kustomization_key in _unique_keys(a.content, b.content):
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in _unique_keys(a_resources, b_resources):
            if resource_key.named_resource in hrs_to_check:
                results.append(resource_key)

    return results
