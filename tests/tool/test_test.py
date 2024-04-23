"""Tests for the flux-local `test` command."""

import pytest

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["tests/testdata/cluster"]),
    ],
    ids=["cluster"],
)
async def test_test_ks(args: list[str]) -> None:
    """Test test kustomization commands."""
    result = await run_command(["test"] + args)
    assert "passed" in result


@pytest.mark.parametrize(
    ("args"),
    [
        (["tests/testdata/cluster"]),
        (["tests/testdata/cluster2"]),
        (["--sources", "cluster=tests/testdata/cluster3", "tests/testdata/cluster3"]),
        (["--enable-kyverno", "tests/testdata/cluster"]),
        (["--enable-kyverno", "tests/testdata/cluster2"]),
        (["tests/testdata/cluster9/clusters/dev"]),
    ],
    ids=["cluster", "cluster2", "cluster3", "policy", "policy-cluster2", "cluster9"],
)
async def test_test_hr(args: list[str]) -> None:
    """Test test helmrelease commands."""
    result = await run_command(["test", "--enable-helm"] + args)
    assert "passed" in result
