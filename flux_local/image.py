"""Helper functions for working with container images."""

import logging
from typing import Any

from . import git_repo, manifest

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
]
IMAGE_KEY = "image"


def _extract_images(doc: dict[str, Any]) -> set[str]:
    """Extract the image from a Kubernetes object."""
    images: set[str] = set({})
    for key, value in doc.items():
        if key == IMAGE_KEY:
            images.add(value)
        elif isinstance(value, dict):
            images.update(_extract_images(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    images.update(_extract_images(item))
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
            images = _extract_images(doc)
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
