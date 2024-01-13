"""Library for generating kustomize commands to build local cluster resources.

Kustomize build can be used to apply overlays and output a set of resources or artifacts
for the cluster that can be either be parsed directly or piped into additional
commands for processing and filtering by kustomize grep.

This example returns the objects inside a Kustomization using `kustomize build`:
```python
from flux_local import kustomize

objects = await kustomize.build('/path/to/objects').objects()
for object in objects:
    print(f"Found object {object['apiVersion']} {object['kind']}")
```

You can also filter documents to specific resource types or other fields:
```python
from flux_local import kustomize

objects = await kustomize.build('/path/to/objects').grep('kind=ConfigMap').objects()
for object in objects:
    print(f"Found ConfigMap: {object['metadata']['name']}")
```

It is also possible to find bare objects without a Kustomization:
```python
from flux_local import kustomize

objects = await kustomize.grep('kind=ConfigMap', '/path/to/objects').objects()
for object in objects:
    print(f"Found ConfigMap: {object['metadata']['name']}")
```

You can apply kyverno policies to the objects with the `validate` method.
"""

from aiofiles.ospath import isdir
import logging
from pathlib import Path
import tempfile
from typing import Any, AsyncGenerator

import yaml

from . import manifest
from .command import Command, run_piped, Task, format_path
from .exceptions import (
    InputException,
    KustomizeException,
    KyvernoException,
    KustomizePathException,
)
from .manifest import Kustomization

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "flux_build",
    "grep",
    "Kustomize",
]

KUSTOMIZE_BIN = "kustomize"
KYVERNO_BIN = "kyverno"
FLUX_BIN = "flux"
HELM_RELEASE_KIND = "HelmRelease"


class Kustomize:
    """Library for issuing a kustomize command."""

    def __init__(self, cmds: list[Task]) -> None:
        """Initialize Kustomize, used internally for copying object."""
        self._cmds = cmds

    def grep(self, expr: str, invert: bool = False) -> "Kustomize":
        """Filter resources based on an expression.

        Example expressions:
          `kind=HelmRelease`
          `metadata.name=redis`
        """
        out = [KUSTOMIZE_BIN, "cfg", "grep", expr]
        if invert:
            out.append("--invert-match")
        return Kustomize(self._cmds + [Command(out, exc=KustomizeException)])

    def grep_helm_release(
        self, helm_release: manifest.HelmRelease | None = None, invert: bool = False
    ) -> "Kustomize":
        """Filter the resources based on the specified HelmRelease."""
        if helm_release:
            if invert:
                raise InputException(
                    "Must specify either helm_release or invert but not both"
                )
            return (
                self.grep(f"metadata.namespace=^{helm_release.namespace}$")
                .grep(f"metadata.name=^{helm_release.name}$")
                .grep(f"kind=^{HELM_RELEASE_KIND}$")
            )
        if invert:
            return self.grep(f"kind=^{HELM_RELEASE_KIND}$", invert=True)
        raise InputException("Must specify either helm_release or invert")

    async def run(self) -> str:
        """Run the kustomize command and return the output as a string."""
        return await run_piped(self._cmds)

    async def _docs(
        self, target_namespace: str | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run the kustomize command and return the result documents."""
        out = await self.run()
        for doc in yaml.safe_load_all(out):
            if target_namespace is not None:
                doc = update_namespace(doc, target_namespace)
            yield doc

    async def objects(
        self, target_namespace: str | None = None
    ) -> list[dict[str, Any]]:
        """Run the kustomize command and return the result cluster objects as a list."""
        return [doc async for doc in self._docs(target_namespace=target_namespace)]

    def skip_resources(self, kinds: list[str]) -> "Kustomize":
        """Skip resources kinds of the specified types."""
        if not kinds:
            return self
        skip_re = "|".join(kinds)
        return self.grep(f"kind=^({skip_re})$", invert=True)

    async def validate_policies(self, policies: list[manifest.ClusterPolicy]) -> None:
        """Apply kyverno policies to objects built so far."""
        if not policies:
            return
        _LOGGER.debug("Validating policies (len=%d)", len(policies))
        with tempfile.TemporaryDirectory() as tmpdir:
            policyfile = Path(tmpdir) / "policies.yaml"
            policyfile.write_text(
                yaml.dump_all(
                    [policy.doc for policy in policies],
                    sort_keys=False,
                    explicit_start=True,
                )
            )
            await self.validate(policyfile)

    async def validate(self, policy_path: Path) -> None:
        """Apply kyverno policies from the directory to any objects built so far.

        The specified `policy_path` is a file or directory containing policy objects.
        All secrets will stripped since otherwise they fail the kyverno cli.
        """
        kustomize = self.skip_resources([manifest.SECRET_KIND])
        cmds = kustomize._cmds + [  # pylint: disable=protected-access
            Command(
                [
                    KYVERNO_BIN,
                    "apply",
                    str(policy_path),
                    "--resource",
                    "-",
                ],
                exc=KyvernoException,
            ),
        ]
        await run_piped(cmds)

    async def stash(self) -> "Kustomize":
        """Memoize the contents built so far for efficient reuse.

        This is useful to serialize a chain of commands but allow further
        chaining with multiple branches.
        """
        content = await self.run()
        return Kustomize([Stash(content.encode("utf-8"))])


class Stash(Task):
    """A task that memoizes output from a previous command."""

    def __init__(self, out: bytes) -> None:
        """Initialize Stash."""
        self._out = out

    async def run(self, stdin: bytes | None = None) -> bytes:
        """Run the task."""
        return self._out


class FluxBuild(Task):
    """A task that issues a flux build command."""

    def __init__(self, ks: Kustomization, path: Path) -> None:
        """Initialize Build."""
        self._ks = ks
        self._path = path

    async def run(self, stdin: bytes | None = None) -> bytes:
        """Run the task."""
        if stdin is not None:
            raise InputException("Invalid stdin cannot be passed to build command")
        if not await isdir(self._path):
            raise KustomizePathException(
                f"Kustomization '{self._ks.namespaced_name}' path field '{self._ks.path or ''}' is not a directory: {self._path}"
            )

        args = [
            FLUX_BIN,
            "build",
            "ks",
            self._ks.name,
            "--dry-run",
            "--kustomization-file",
            "/dev/stdin",
            "--path",
            str(self._path),
        ]
        if self._ks.namespace:
            args.extend(
                [
                    "--namespace",
                    self._ks.namespace,
                ]
            )
        kustomization_data = yaml.dump_all(
            [self._ks.contents or {}], sort_keys=False, explicit_start=True
        )
        input_ks = str(kustomization_data).encode("utf-8")

        task = Command(args, cwd=None, exc=KustomizeException)
        return await task.run(stdin=input_ks)

    def __str__(self) -> str:
        """Render as a debug string."""
        return f"flux build {format_path(self._path)}"


def flux_build(ks: Kustomization, path: Path) -> Kustomize:
    """Build cluster artifacts from the specified path."""
    return Kustomize(cmds=[FluxBuild(ks, path)])


def grep(expr: str, path: Path, invert: bool = False) -> Kustomize:
    """Filter resources in the specified path based on an expression."""
    args = [KUSTOMIZE_BIN, "cfg", "grep", expr]
    if invert:
        args.append("--invert-match")
    cwd: Path | None = None
    if path.is_absolute():
        args.append(".")
        cwd = path
    else:
        args.append(str(path))
    return Kustomize([Command(args, cwd=cwd, exc=KustomizeException)])


def update_namespace(doc: dict[str, Any], namespace: str) -> dict[str, Any]:
    """Update the namespace of the specified document.

    Will only update the namespace if the doc appears to have a metadata/name.
    """
    if (metadata := doc.get("metadata")) is not None and "name" in metadata:
        doc["metadata"]["namespace"] = namespace
    return doc
