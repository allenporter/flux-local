"""Configuration objects for flux-local."""

from dataclasses import dataclass


@dataclass
class HelmControllerConfig:
    """Configuration for the HelmReleaseController."""

    wipe_secrets: bool = True


@dataclass
class KustomizationControllerConfig:
    """Configuration for the KustomizationController."""

    wipe_secrets: bool = True


@dataclass
class ReadAction:
    """Configuration for reading resources."""

    wipe_secrets: bool = True
