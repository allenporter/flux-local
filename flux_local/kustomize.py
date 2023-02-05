"""Library for generating kustomize commands to build local cluster resources.

Kustomize build can be used to apply overlays and output a set of resources or artifacts
for the cluster that can be either be parsed directly or piped into additional
commands for processing and filtering by kustomize grep.
"""

from pathlib import Path

KUSTOMIZE_BIN = "kustomize"


def build(path: Path) -> list[str]:
    """Generate a kustomize build command for the specified path."""
    return [KUSTOMIZE_BIN, "build", str(path)]


def grep(expr: str, path: Path | None = None, invert: bool = False) -> list[str]:
    """Generate a kustomize grep command to filter resources based on an expression.

    Example expressions:
      `kind=HelmRelease`
      `metadata.name=redis`

    The return value is a set of command args.
    """
    out = [KUSTOMIZE_BIN, "cfg", "grep", expr]
    if invert:
        out.append("--invert-match")
    if path:
        out.append(str(path))
    return out
