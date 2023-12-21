"""Tests for the flux-local diff command.

This uses a separate test harness to create a worktree, modify files, then
run the diff. This is difficult to setup with static testdata since the
Kustomization paths would need to change which makes the resources
look like they belong to a separate cluster.
"""

import pytest
import os

from syrupy.assertion import SnapshotAssertion

from flux_local import git_repo

from . import run_command

CLUSTER_DIR = "tests/testdata/cluster"


# These tests fool codecov so run separately
@pytest.mark.skipif(
    os.environ.get("SKIP_DIFF_TESTS", False),
    reason="SKIP_DIFF_TESTS set in environment",
)
async def test_diff_ks(snapshot: SnapshotAssertion) -> None:
    """Test a diff in resources within a Kustomization."""

    repo = git_repo.git_repo()
    with git_repo.create_worktree(repo) as tree1:
        path_orig = tree1 / CLUSTER_DIR

        with git_repo.create_worktree(repo) as tree2:
            path = tree2 / CLUSTER_DIR

            # Empty out a config map in the cluster
            configmap = path / "apps/prod/configmap.yaml"
            configmap.write_text("")

            result = await run_command(
                [
                    "diff",
                    "ks",
                    "--path",
                    str(path),
                    "--path-orig",
                    str(path_orig),
                ]
            )
    assert result == snapshot
