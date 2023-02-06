"""Library for generating kustomize commands to build local cluster resources.

Kustomize build can be used to apply overlays and output a set of resources or artifacts
for the cluster that can be either be parsed directly or piped into additional
commands for processing and filtering by kustomize grep.
"""

from pathlib import Path
from typing import Generator, Any
import yaml

from . import command

KUSTOMIZE_BIN = "kustomize"


class Kustomize:
    """Library for issuing a kustomize command."""

    def __init__(self, cmds: list[list[str]]) -> None:
        """Initialize Kustomize."""
        self._cmds = cmds

    @classmethod
    def build(cls, path: Path) -> 'Kustomize':
        """Build cluster artifacts from the specified path."""
        return Kustomize(cmds=[[KUSTOMIZE_BIN, "build", str(path)]])

    def grep(self, expr: str, path: Path | None = None, invert: bool = False) -> 'Kustomize':
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

    async def _docs(self) -> Generator[dict[str, Any], None, None]:
        """Run the kustomize command and return the result documents."""
        out = await self.run()
        for doc in yaml.safe_load_all(out):
            yield doc

    async def docs(self) -> list[dict[str, Any]]:
        """Run the kustomize command and return the result documents as a list."""
        return [ doc async for doc in self._docs() ]
