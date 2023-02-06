"""Library for generating kustomize commands to build local cluster resources.

Kustomize build can be used to apply overlays and output a set of resources or artifacts
for the cluster that can be either be parsed directly or piped into additional
commands for processing and filtering by kustomize grep.

This example returns the objects inside a Kustomization using `kustomize build`:
```python
from flux_local.kustomize import Kustomize

objects = await Kustomize.build('/path/to/objects').objects()
for object in objects:
    print(f"Found object {object['apiVersion']} {object['kind']}")
```

You can also filter documents to specific resource types or other fields:
```python
from flux_local.kustomize import Kustomize

objects = await Kustomize.build('/path/to/objects').grep('kind=ConfigMap').objects()
for object in objects:
    print(f"Found ConfigMap: {object['metadata']['name']}")
```

You can apply kyverno policies to the objects with the `validate` method.
"""

from pathlib import Path
from typing import Any, AsyncGenerator

import yaml

from . import command

KUSTOMIZE_BIN = "kustomize"
KYVERNO_BIN = "kyverno"


class Kustomize:
    """Library for issuing a kustomize command."""

    def __init__(self, cmds: list[list[str]]) -> None:
        """Initialize Kustomize."""
        self._cmds = cmds

    @classmethod
    def build(cls, path: Path) -> "Kustomize":
        """Build cluster artifacts from the specified path."""
        return Kustomize(cmds=[[KUSTOMIZE_BIN, "build", str(path)]])

    def grep(
        self, expr: str, path: Path | None = None, invert: bool = False
    ) -> "Kustomize":
        """Filter resources based on an expression.

        Example expressions:
          `kind=HelmRelease`
          `metadata.name=redis`
        """
        out = [KUSTOMIZE_BIN, "cfg", "grep", expr]
        if invert:
            out.append("--invert-match")
        if path:
            out.append(str(path))
        return Kustomize(self._cmds + [out])

    async def run(self) -> str:
        """Run the kustomize command and return the output as a string."""
        return await command.run_piped(self._cmds)

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
            [
                KYVERNO_BIN,
                "apply",
                str(policy_path),
                "--resource",
                "-",
            ],
        ]
        await command.run_piped(cmds)
