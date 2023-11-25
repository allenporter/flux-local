"""Tests for the flux-local `diagnostics` command."""

import pathlib
import tempfile
from typing import Any

from syrupy.assertion import SnapshotAssertion
import yaml
import pytest

from . import run_command


async def test_diagnostics(snapshot: SnapshotAssertion) -> None:
    """Test test get ks commands."""
    result = await run_command(["diagnostics"])
    assert result == snapshot


@pytest.mark.parametrize(
    ("data"),
    [
        ([["entry"]]),
        (["entry", "var"]),
    ],
    ids=["list", "scalar"],
)
async def test_invalid_mapping(data: Any, snapshot: SnapshotAssertion) -> None:
    """Test test get ks commands."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        contents = yaml.dump_all(data)

        example_file = pathlib.Path(tmp_dir) / "example.yaml"
        example_file.write_text(contents)

        result = await run_command(["diagnostics", "--path", tmp_dir])
        result = result.replace(str(example_file), "<<TEST_FILE>>")
        assert result == snapshot
