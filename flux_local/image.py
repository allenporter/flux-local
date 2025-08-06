"""Helper functions for working with container images."""

import logging
from typing import Any

from . import git_repo, manifest
from .exceptions import FluxException

_LOGGER = logging.getLogger(__name__)


# Object types that may have container images.
KINDS = [
    "Pod",
    "Deployment",
    "StatefulSet",
    "ReplicaSet",
    "DaemonSet",
    "CronJob",
    "Job",
    "ReplicationController",
    "EMQX",  # apps.emqx.io/v2beta1
    "Cluster",  # postgresql.cnpg.io/v1
    "CephCluster",  # ceph.rook.io/v1
    "Alertmanager",  # monitoring.coreos.com/v1
    "Prometheus",  # monitoring.coreos.com/v1
    "AutoscalingRunnerSet",  # actions.github.com/v1alpha1
]

# Default image key for most object types.
IMAGE_KEY = "image"

# Override the default image key for some object types.
KINDS_IMAGE_KEY = {"Cluster": "imageName"}


def _extract_images(kind: str, doc: dict[str, Any]) -> set[str]:
    """Extract the image from a Kubernetes object."""
    images: set[str] = set({})
    image_key = KINDS_IMAGE_KEY.get(kind) or IMAGE_KEY

    for key, value in doc.items():
        if key == image_key:
            if isinstance(value, dict) and "reference" in value:
                value = value.get("reference")  # https://kubernetes.io/blog/2024/08/16/kubernetes-1-31-image-volume-source/
            if not isinstance(value, str):
                raise ValueError(
                    f"Expected string for image key '{image_key}', got type {type(value).__name__}: {value}"
                )
            images.add(value)
        elif isinstance(value, dict):
            images.update(_extract_images(kind, value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    images.update(_extract_images(kind, item))

    return images


class ImageVisitor:
    """Helper that visits container image related objects.

    This tracks the container images used by the kustomizations and HelmReleases
    so they can be dumped for further verification.
    """

    def __init__(self) -> None:
        """Initialize ImageVisitor."""
        self.images: dict[str, set[str]] = {}

    def repo_visitor(self) -> git_repo.DocumentVisitor:
        """Return a git_repo.DocumentVisitor that points to this object."""

        def add_image(name: str, doc: dict[str, Any]) -> None:
            """Visitor function to find relevant images and record them for later inspection.

            Updates the image set with the images found in the document.
            """
            kind: str = doc["kind"]
            try:
                images = _extract_images(kind, doc)
            except ValueError as err:
                raise FluxException(
                    f"Error extracting images from document '{name}' of kind '{kind}': {err}"
                )
            if not images:
                return
            if name in self.images:
                self.images[name].update(images)
            else:
                self.images[name] = set(images)

        return git_repo.DocumentVisitor(kinds=KINDS, func=add_image)

    def update_manifest(self, manifest: manifest.Manifest) -> None:
        """Update the manifest with the images found in the repo."""
        for cluster in manifest.clusters:
            for kustomization in cluster.kustomizations:
                if images := self.images.get(kustomization.namespaced_name):
                    kustomization.images = list(images)
                    kustomization.images.sort()
