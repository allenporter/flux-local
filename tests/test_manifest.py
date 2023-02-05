import json
import os
import tempfile

import flux_local.manifest


def test_generate_manifest():
    """Test generating a manifest file."""
    # Setup
    project_name = "my-project"
    project_version = "0.0.1"
    description = "My project is a command line tool that does x, y, and z."
    files = [
        {"name": "main.py", "description": "The main program file."},
        {"name": "README.md", "description": "The project README file."},
    ]

    # Run
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = os.path.join(temp_dir, "manifest.json")
        manifest.generate_manifest(
            project_name, project_version, description, files, output_path
        )

        # Verify
        with open(output_path) as f:
            data = json.load(f)

        assert data["name"] == project_name
        assert data["version"] == project_version
        assert data["description"] == description
        assert len(data["files"]) == 2
        assert data["files"][0]["name"] == "main.py"
        assert data["files"][0]["description"] == "The main program file."
        assert data["files"][1]["name"] == "README.md"
        assert data["files"][1]["description"] == "The project README file."
