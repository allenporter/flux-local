"""Library for operating on a local repo."""

import os
from functools import cache
from pathlib import Path

import git
import yaml

from .manifest import Manifest


@cache
def repo_root() -> Path:
    """Return the local github repo path."""
    git_repo = git.Repo(os.getcwd(), search_parent_directories=True)
    return Path(git_repo.git.rev_parse("--show-toplevel"))


def manifest(manifest_file: Path) -> Manifest:
    """Return the contents of the manifest file from the local repo."""
    contents = manifest_file.read_text()
    doc = next(yaml.load_all(contents, Loader=yaml.Loader))
    if "spec" not in doc:
        raise ValueError("Manifest file malformed, missing 'spec'")
    return Manifest(clusters=doc["spec"])
