"""Module for computing resource diffs.

This is used internally, primarily by the diff tool.
"""

from collections.abc import Iterable
from dataclasses import asdict
import difflib
import logging
import pathlib
import tempfile
from typing import Generator, Any, AsyncGenerator, TypeVar
import yaml


from . import command
from .visitor import ObjectOutput, ResourceKey

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

    diffs = []
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
        yield yaml.dump(diffs, sort_keys=False, explicit_start=True, default_style=None)


def get_helm_release_diff_keys(a: ObjectOutput, b: ObjectOutput) -> list[ResourceKey]:
    """Return HelmRelease resource keys with diffs, by cluster."""
    results: list[ResourceKey] = []
    for kustomization_key in _unique_keys(a.content, b.content):
        _LOGGER.debug("Diffing results for Kustomization %s", kustomization_key)
        a_resources = a.content.get(kustomization_key, {})
        b_resources = b.content.get(kustomization_key, {})
        for resource_key in _unique_keys(a_resources, b_resources):
            if resource_key.kind != "HelmRelease":
                continue
            if a_resources.get(resource_key) != b_resources.get(resource_key):
                results.append(resource_key)
    return results
