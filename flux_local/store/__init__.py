"""
The store module provides a central, type-safe repository for tracking the state, definitions,
and artifacts of Kubernetes custom resources (CRs) during a flux-local run.

- Uses NamedResource as the key for all objects.
- Stores values as dataclass instances from manifest.py for type safety.
- Provides query and update APIs for controllers (e.g., SourceController, KustomizationController).

This abstract interface allows for various implementations (in-memory, persistent, etc.).
"""

from .store import Store, StoreEvent
from .in_memory import InMemoryStore
from .artifact import Artifact
from .status import Status, StatusInfo

__all__ = [
    "Store",
    "StoreEvent",
    "InMemoryStore",
    "Artifact",
    "Status",
    "StatusInfo",
]
