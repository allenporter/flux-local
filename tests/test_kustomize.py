"""Tests for cmd library."""

from pathlib import Path

from flux_local import command, kustomize

TESTDATA_DIR = Path("tests/testdata")


async def test_build() -> None:
    """Test a kustomize build command."""
    result = await command.run(kustomize.build(TESTDATA_DIR / "repo"))
    assert "Secret" in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo.golden").read_text()


async def test_grep() -> None:
    """Test a kustomize build command."""
    result = await command.run_piped(
        [
            kustomize.build(TESTDATA_DIR / "repo"),
            kustomize.grep("kind=ConfigMap"),
        ]
    )
    assert "Secret" not in result
    assert "ConfigMap" in result
    assert result == (TESTDATA_DIR / "repo.configmap.golden").read_text()
