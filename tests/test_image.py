"""Tests for image."""

from pathlib import Path
from typing import Any

import pytest
from syrupy.assertion import SnapshotAssertion

from flux_local.git_repo import build_manifest, ResourceSelector, PathSelector
from flux_local.image import ImageVisitor
from flux_local.exceptions import FluxException

TESTDATA = Path("tests/testdata/cluster8")
CWD = Path.cwd()


@pytest.mark.parametrize(
    ("test_path", "expected"),
    [
        (
            CWD / "tests/testdata/cluster8",
            {
                "flux-system/apps": {
                    "alpine",
                    "busybox",
                    "ghcr.io/buroa/qbrr:latest",
                    "ghcr.io/home-operations/qbittorrent:latest",
                }
            },
        ),
        (
            CWD / "tests/testdata/cluster7",
            {},
        ),
        (
            CWD / "tests/testdata/cluster",
            {
                "flux-system/infra-configs": {
                    "ceph/ceph:v16.2.6",
                    "ghcr.io/cloudnative-pg/postgis:17-3.4",
                },
            },
        ),
    ],
)
async def test_image_visitor(
    snapshot: SnapshotAssertion, test_path: str, expected: dict[str, Any]
) -> None:
    """Tests for building the manifest."""

    image_visitor = ImageVisitor()
    query = ResourceSelector(
        path=PathSelector(Path(test_path)),
        doc_visitor=image_visitor.repo_visitor(),
    )
    await build_manifest(selector=query)

    assert image_visitor.images == expected


@pytest.mark.parametrize(
    ("test_path", "expected_error"),
    [
        (
            CWD / "tests/testdata/image_error",
            "Error extracting images from document 'flux-system/apps' of kind 'Pod': Expected string for image key 'image', got type dict: {'repository': 'busybox', 'tag': 16}",
        ),
    ],
    ids=["image_error"],
)
async def test_image_visitor_failure(
    snapshot: SnapshotAssertion, test_path: str, expected_error: str
) -> None:
    """Tests for building the manifest."""

    image_visitor = ImageVisitor()
    query = ResourceSelector(
        path=PathSelector(Path(test_path)),
        doc_visitor=image_visitor.repo_visitor(),
    )
    with pytest.raises(FluxException, match=expected_error):
        await build_manifest(selector=query)
