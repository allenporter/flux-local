"""Tests for image."""

from pathlib import Path
from typing import Any

import pytest
from syrupy.assertion import SnapshotAssertion

from flux_local.git_repo import build_manifest, ResourceSelector, PathSelector
from flux_local.image import ImageVisitor

TESTDATA = Path("tests/testdata/cluster8")
CWD = Path.cwd()


@pytest.mark.parametrize(
    ("test_path", "expected"),
    [
        (
            CWD / "tests/testdata/cluster8",
            {"flux-system/apps": {"alpine", "busybox"}},
        ),
        (
            CWD / "tests/testdata/cluster7",
            {},
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
