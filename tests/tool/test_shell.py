"""Tests for the flux-local shell command."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from flux_local.store import InMemoryStore
from flux_local.tool.shell import FluxShell


@pytest.fixture
def store() -> InMemoryStore:
    """Return an in-memory store for testing."""
    return InMemoryStore()


@pytest.fixture
def shell_and_output(store: InMemoryStore) -> tuple[Any, StringIO]:
    """Return a FluxShell instance for testing with captured output."""

    # Create a mock store with some test data if needed
    if not store.list_objects():
        # Add test data to the store if empty
        from flux_local.manifest import Kustomization, GitRepository, GitRepositoryRef
        from pathlib import Path

        path = Path("/test/path")
        git_repo = GitRepository(
            name="test-repo",
            namespace="test-ns",
            url=str(path.absolute()),
            ref=GitRepositoryRef(branch="main"),
        )
        store.add_object(git_repo)

        kustomization = Kustomization(
            name="test-ks",
            namespace="test-ns",
            path=".",
            source_kind="GitRepository",
            source_name="test-repo",
            source_namespace="test-ns",
        )
        store.add_object(kustomization)

    stdout = StringIO()
    stderr = StringIO()
    shell = FluxShell(store=store, path="/test/path", stdout=stdout, stderr=stderr)
    return shell, stdout


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("help get", ["Get resources", "Examples"]),
        ("get invalid", ["Unknown resource type: invalid"]),
        ("get kustomizations", ["No resources found"]),
        ("exit", ["Exiting flux-local shell"]),
        ("quit", ["Exiting flux-local shell"]),
    ],
    ids=["help", "invalid-resource", "empty-kustomizations", "exit", "quit"],
)
async def test_shell_commands(
    shell_and_output: tuple[Any, StringIO], command: str, expected: list[str]
) -> None:
    """Test basic shell commands."""
    shell, output = shell_and_output

    # For get commands, mock the store to return empty list
    if command.startswith("get ") and not command.startswith("get invalid"):
        with patch.object(shell.store, "list_objects", return_value=[]):
            shell.onecmd(command)
    else:
        shell.onecmd(command)

    # Check both stdout and stderr for the expected text
    output_str = output.getvalue()
    error_str = shell.stderr.getvalue()
    for text in expected:
        if text in output_str or text in error_str:
            continue
        assert False, (
            f"Expected '{text}' in output or error.\nOutput: {output_str}\nError: {error_str}"
        )


def test_complete_get(shell_and_output: tuple[Any, StringIO]) -> None:
    """Test tab completion for get command."""
    shell, _ = shell_and_output
    completions = shell.complete_get("", "get ", 0, 0)
    assert "kustomization" in completions
    assert "helmrelease" in completions


@pytest.mark.parametrize(
    ("output_format", "expected"),
    [
        (None, "No resources found"),
        ("yaml", "No resources found"),
        ("json", "No resources found"),
    ],
    ids=["default", "yaml", "json"],
)
async def test_get_output_formats(
    shell_and_output: tuple[Any, StringIO], output_format: str | None, expected: str
) -> None:
    """Test get command with different output formats."""
    shell, output = shell_and_output

    # Setup - mock the store to return empty list
    with patch.object(
        shell.store, "list_objects", return_value=[]
    ) as mock_list_objects:
        cmd = "get kustomizations"
        if output_format:
            cmd += f" -o {output_format}"

        shell.onecmd(cmd)

        # Verify the store was called with correct arguments
        mock_list_objects.assert_called_once_with(kind="Kustomization")

        # Verify the output contains expected text
        output_str = output.getvalue()
        assert expected in output_str, f"Expected '{expected}' in output: {output_str}"


async def test_get_resources(
    shell_and_output: tuple[Any, StringIO],
    store: InMemoryStore,
    snapshot: SnapshotAssertion,
) -> None:
    """Test getting resources from the store."""
    shell, output = shell_and_output

    # Create a mock kustomization object with the required attributes
    kustomization = MagicMock()
    kustomization.name = "test-ks"
    kustomization.namespace = "test-ns"
    kustomization.path = "./test"
    kustomization.to_dict.return_value = {
        "name": "test-ks",
        "namespace": "test-ns",
        "path": "./test",
    }
    store.add_object(kustomization)

    # Test getting the kustomization directly through _get_resources
    with patch.object(
        shell.store, "list_objects", return_value=[kustomization]
    ) as mock_list_objects:
        resources = shell._get_resources("kustomization")
        mock_list_objects.assert_called_once_with(kind="Kustomization")

        # Verify the resources
        assert len(resources) == 1
        assert resources[0].name == "test-ks"
        assert resources[0].namespace == "test-ns"

    # Reset the output and error buffers
    output.truncate(0)
    output.seek(0)
    shell.stderr.truncate(0)
    shell.stderr.seek(0)

    # Test getting the kustomization through the shell command with JSON output
    with patch.object(
        shell.store, "list_objects", return_value=[kustomization]
    ) as mock_list_objects:
        shell.onecmd("get kustomizations -o json")
        mock_list_objects.assert_called_once_with(kind="Kustomization")

        # Get the output and parse it as JSON
        output_str = output.getvalue()
        error_str = shell.stderr.getvalue()

        print(f"=== STDOUT ===\n{output_str}\n=== END STDOUT ===")
        print(f"=== STDERR ===\n{error_str}\n=== END STDERR ===")

        # Parse the JSON output
        import json

        output_data = json.loads(output_str)

        # Verify the output contains the expected kustomization
        assert len(output_data) == 1, (
            f"Expected 1 kustomization, got {len(output_data)}"
        )
        assert output_data[0]["name"] == "test-ks", (
            f"Expected name 'test-ks', got {output_data[0].get('name')}"
        )
        assert output_data[0]["namespace"] == "test-ns", (
            f"Expected namespace 'test-ns', got {output_data[0].get('namespace')}"
        )
        assert output_data[0]["path"] == "./test", (
            f"Expected path './test', got {output_data[0].get('path')}"
        )
