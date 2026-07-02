"""
.. include:: ../README.md
"""

import warnings

warnings.warn(
    "flux-local is deprecated and has been replaced by flate and konflate. "
    "Please migrate to these modern, actively maintained alternatives.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "git_repo",
    "manifest",
    "kustomize",
    "helm",
    "exceptions",
]
