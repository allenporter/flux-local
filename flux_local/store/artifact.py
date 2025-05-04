"""Artifact representation."""

from abc import ABC
from dataclasses import dataclass


@dataclass
class Artifact(ABC):
    path: str
    revision: str
