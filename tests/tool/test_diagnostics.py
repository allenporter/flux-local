"""Tests for the flux-local `diagnostics` command."""

import pathlib
import tempfile

from syrupy.assertion import SnapshotAssertion
import yaml
import pytest

from . import run_command


async def test_diagnostics(snapshot: SnapshotAssertion) -> None:
    """Test test get ks commands."""
    result = await run_command(["diagnostics"])
    assert result == snapshot


@pytest.mark.parametrize(
    ("contents"),
    [
        (yaml.dump_all([["entry"]]).encode()),
        (yaml.dump_all(["entry", "var"]).encode()),
        (b"\x00\x00\x00"),
    ],
    ids=["list", "scalar", "not-yaml"],
)
async def test_invalid_mapping(contents: bytes, snapshot: SnapshotAssertion) -> None:
    """Test test get ks commands."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        example_file = pathlib.Path(tmp_dir) / "example.yaml"
        example_file.write_bytes(contents)

        result = await run_command(["diagnostics", "--path", tmp_dir])
        result = result.replace(str(example_file), "<<TEST_FILE>>")
        assert result == snapshot
