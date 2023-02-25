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

import aiofiles
from aiofiles.os import listdir  # type: ignore[attr-defined]
from aiofiles.ospath import isdir, exists
import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator

import yaml

from . import manifest
from .command import Command, run_piped, Task, CommandException

__all__ = [
    "build",
    "grep",
    "Kustomize",
]

KUSTOMIZE_BIN = "kustomize"
KYVERNO_BIN = "kyverno"
HELM_RELEASE_KIND = "HelmRelease"
KUSTOMIZE_FILES = ["kustomization.yaml", "kustomization.yml", "Kustomization"]


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

        The specified `policy_path` is a file or directory containing policy objects.
        All secrets will stripped since otherwise they fail the kyverno cli.
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

    async def stash(self, tmp_file: Path) -> "Kustomize":
        """Output the contents built so far to disk for efficient reuse.

        This is useful to serialize a chain of commands but allow further
        chaining with multiple branches.
        """
        content = await self.run()
        tmp_file.write_text(content)
        return Kustomize([Command(["cat", str(tmp_file)])])


class Build(Task):
    """A task that issues a build command, handling implicit Kustomizations."""

    def __init__(self, path: Path) -> None:
        """Initialize Build."""
        self._path = path

    async def run(self, stdin: bytes | None = None) -> bytes:
        """Run the task."""
        if stdin is not None:
            raise ValueError("Invalid stdin cannot be passed to build command")

        if not await isdir(self._path):
            raise CommandException(f"Specified path is not a directory: {self._path}")
        if not await can_kustomize_dir(self._path):
            # Attempt to effectively generate a kustomization.yaml on the fly
            # mirroring the behavior of flux
            return await fluxtomize(self._path)

        args = [KUSTOMIZE_BIN, "build"]
        cwd: Path | None = None
        if self._path.is_absolute():
            cwd = self._path
        else:
            args.append(str(self._path))
        task = Command(args, cwd=cwd)
        return await task.run()


async def can_kustomize_dir(path: Path) -> bool:
    """Return true if a kustomize file exists for the specified directory."""
    for name in KUSTOMIZE_FILES:
        if await exists(path / name):
            return True
    return False


async def yaml_load_all(path: Path) -> list[dict[str, Any]]:
    """Load all documents from the file."""
    async with aiofiles.open(path) as f:
        contents = await f.read()
        return list(yaml.load_all(contents, Loader=yaml.Loader))


async def fluxtomize(path: Path) -> bytes:
    """Create a synthentic Kustomization file and attempt to build it.

    This is similar to the behavior of flux, which kustomize does not support
    directly. Every yaml file found is read and written to the output. Every
    directory that can be kustomized is built using the CLI like build()
    """
    # Every file resource is read and output
    # Every directory is kustomized
    tasks = []
    filenames = await listdir(path)
    for filename in filenames:
        new_path = path / filename
        if new_path.is_dir() and await can_kustomize_dir(new_path):
            tasks.append(build(new_path).objects())
        elif filename.endswith(".yaml") or filename.endswith(".yml"):
            tasks.append(yaml_load_all(new_path))
        else:
            continue

    results = await asyncio.gather(*tasks)
    docs = []
    for result in results:
        docs.extend(result)
    out = yaml.dump_all(docs, sort_keys=False, explicit_start=True)
    return str(out).encode("utf-8")


def build(path: Path) -> Kustomize:
    """Build cluster artifacts from the specified path."""
    return Kustomize(cmds=[Build(path)])


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
