"""Artifact representation."""

from abc import ABC
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Artifact(ABC):
    """Base class for all artifacts."""
