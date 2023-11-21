"""Tests for the flux-local `diff hr` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args", "env"),
    [
        (["podinfo", "-n", "podinfo", "--path", "tests/testdata/cluster/"], {}),
        (
            [
                "renovate",
                "-n",
                "default",
                "--path",
                "tests/testdata/cluster6/",
                "-a",
                "batch/v1/CronJob",
            ],
            {},
        ),
        (
            [
                "podinfo",
                "-n",
                "podinfo",
                "--path",
                "tests/testdata/cluster/",
                "-o",
                "yaml",
            ],
            {},
        ),
        (["podinfo", "--all-namespaces", "--path", "tests/testdata/cluster/"], {}),
        (["--all-namespaces", "--path", "tests/testdata/cluster/"], {}),
        (
            ["podinfo", "-n", "podinfo", "--path", "tests/testdata/cluster/"],
            {"DIFF": "diff"},
        ),
        (
            [
                "podinfo",
                "-n",
                "podinfo",
                "--path",
                "tests/testdata/cluster/",
                "--limit-bytes",
                "20",
            ],
            {"DIFF": "diff"},
        ),
        (
            [
                "podinfo",
                "-n",
                "podinfo",
                "--path",
                "tests/testdata/cluster/",
                "--limit-bytes",
                "20000",
            ],
            {},
        ),
        (["example", "--path", "tests/testdata/cluster"], {}),
        (["example", "--namespace", "example", "--path", "tests/testdata/cluster"], {}),
        (["example", "--all-namespaces", "--path", "tests/testdata/cluster"], {}),
    ],
    ids=[
        "podinfo",
        "cluster6",
        "yaml",
        "all-namespaces",
        "all",
        "external",
        "external-limit-bytes",
        "limit-bytes",
        "not-found",
        "not-found-namespace",
        "not-found-all-namespaces",
    ],
)
async def test_diff_hr(
    args: list[str], env: dict[str, str], snapshot: SnapshotAssertion
) -> None:
    """Test test diff hr commands."""
    result = await run_command(["diff", "hr"] + args, env=env)
    assert result == snapshot
