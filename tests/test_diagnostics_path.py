"""Tests for flux-local diagnostics against a nonexistent path."""

import pytest

from flux_local.exceptions import FluxException
from flux_local.tool.diagnostics import DiagnosticsAction


async def test_diagnostics_nonexistent_path(tmp_path, capsys):
    """A path that does not exist must fail, not report OK."""
    missing = tmp_path / "does-not-exist"
    assert not missing.exists()
    with pytest.raises(FluxException):
        await DiagnosticsAction().run(path=str(missing))
    out, _ = capsys.readouterr()
    assert "[DIAGNOSTICS FAIL]" in out
