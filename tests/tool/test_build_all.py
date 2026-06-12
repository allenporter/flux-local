"""Tests for the flux-local `build` command."""

import pytest

from syrupy.assertion import SnapshotAssertion

from . import run_command


@pytest.mark.parametrize(
    ("args"),
    [
        (["tests/testdata/cluster/"]),
        (["tests/testdata/cluster2/"]),
        (["tests/testdata/cluster3/"]),
        (["tests/testdata/cluster6/"]),
        (["--enable-helm", "--skip-crds", "tests/testdata/cluster/"]),
        (
            [
                "--enable-helm",
                "--skip-crds",
                "tests/testdata/cluster6/",
                "-a",
                "batch/v1/CronJob",
            ]
        ),
        (["--enable-helm", "--no-skip-secrets", "tests/testdata/cluster8/"]),
        (
            [
                "--enable-helm",
                "--no-skip-secrets",
                "tests/testdata/cluster9/clusters/dev",
            ]
        ),
    ],
    ids=[
        "build",
        "build-cluster2",
        "build-cluster3",
        "build-cluster6",
        "build-helm-cluster",
        "build-helm-cluster6",
        "build-helm-cluster8-valuesFrom",
        "build-helm-cluster9",
    ],
)
async def test_build_all(args: list[str], snapshot: SnapshotAssertion) -> None:
    """Test build commands."""
    result = await run_command(["build", "all"] + args)
    assert result == snapshot


async def test_build_all_with_extra_configmap_file() -> None:
    """Test build all can use extra ConfigMaps for postBuild substitutions."""
    result = await run_command(
        [
            "build",
            "all",
            "--extra-configmap-file",
            "tests/testdata/extra/cluster-config-override.yaml",
            "tests/testdata/cluster/",
        ]
    )
    assert "override-example.org-staging" in result
    assert "commonName: override.example.org" in result
