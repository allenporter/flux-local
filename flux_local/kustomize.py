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

from pathlib import Path
from typing import Any, AsyncGenerator

import yaml

from . import manifest
from .command import Command, run_piped

__all__ = [
    "build",
    "grep",
    "Kustomize",
]

KUSTOMIZE_BIN = "kustomize"
KYVERNO_BIN = "kyverno"
HELM_RELEASE_KIND = "HelmRelease"


class Kustomize:
    """Library for issuing a kustomize command."""

    def __init__(self, cmds: list[Command]) -> None:
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
        return Kustomize(self._cmds + [Command(out)])

    def grep_helm_release(
        self, helm_release: manifest.HelmRelease | None = None, invert: bool = False
    ) -> "Kustomize":
        """Filter the resources based on the specified HelmRelease."""
        if helm_release:
            if invert:
                raise ValueError(
                    "Must specify either helm_release or invert but not both"
                )
            return (
                self.grep(f"metadata.namespace=^{helm_release.namespace}$")
                .grep(f"metadata.name=^{helm_release.name}$")
                .grep(f"kind=^{HELM_RELEASE_KIND}$")
            )
        if invert:
            return self.grep(f"kind=^{HELM_RELEASE_KIND}$", invert=True)
        raise ValueError("Must specify either helm_release or invert")

    async def run(self) -> str:
        """Run the kustomize command and return the output as a string."""
        return await run_piped(self._cmds)

    async def _docs(self) -> AsyncGenerator[dict[str, Any], None]:
        """Run the kustomize command and return the result documents."""
        out = await self.run()
        for doc in yaml.safe_load_all(out):
            yield doc

    async def objects(self) -> list[dict[str, Any]]:
        """Run the kustomize command and return the result cluster objects as a list."""
        return [doc async for doc in self._docs()]

    async def validate(self, policy_path: Path) -> None:
        """Apply kyverno policies from the directory to any objects built so far.

        The specified `policy_path` is a file or directory containing policy objects. All
        secrets will stripped since otherwise they fail the kyverno cli.
        """
        kustomize = self.grep("kind=^Secret$", invert=True)
        cmds = kustomize._cmds + [  # pylint: disable=protected-access
            Command(
                [
                    KYVERNO_BIN,
                    "apply",
                    str(policy_path),
                    "--resource",
                    "-",
                ]
            ),
        ]
        await run_piped(cmds)


def build(path: Path) -> Kustomize:
    """Build cluster artifacts from the specified path."""
    args = [KUSTOMIZE_BIN, "build"]
    cwd: Path | None = None
    if path.is_absolute():
        cwd = path
    else:
        args.append(str(path))
    return Kustomize(cmds=[Command(args, cwd=cwd)])


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
    return Kustomize([Command(args, cwd=cwd)])
