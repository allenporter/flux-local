"""Command line tool for diagnosing configuration problems in the cluster."""

import logging
from argparse import ArgumentParser, _SubParsersAction as SubParsersAction
from typing import cast
import os
import pathlib
import yaml


_LOGGER = logging.getLogger(__name__)

FAIL = "[DIAGNOSTICS FAIL]"
OK = "[DIAGNOSTICS OK]"
IGNORE_DIRS = {"venv", ".venv"}


class DiagnosticsAction:
    """Flux-local diagnostics action."""

    @classmethod
    def register(
        cls, subparsers: SubParsersAction  # type: ignore[type-arg]
    ) -> ArgumentParser:
        """Register the subparser commands."""
        args = cast(
            ArgumentParser,
            subparsers.add_parser(
                "diagnostics",
                help="Print information about local flux that can diagnose issues",
                description="Print information about the local cluster to aid with diagnosing problems.",
            ),
        )
        args.add_argument(
            "--path",
            help="Optional path with flux Kustomization resources (multi-cluster ok)",
            type=pathlib.Path,
            default=None,
            nargs="?",
        )
        args.set_defaults(cls=cls)
        return args

    async def run(  # type: ignore[no-untyped-def]
        self,
        **kwargs,  # pylint: disable=unused-argument
    ) -> None:
        """Async Action implementation."""
        path = kwargs.get("path") or "."

        # Parse directories to ignore from `.krmignore`
        krmignore = pathlib.Path(path) / ".krmignore"
        ignoredirs = list(IGNORE_DIRS)
        if krmignore.exists():
            with krmignore.open() as fd:
                ignoredirs.extend(
                    str(pathlib.Path(line.strip())) for line in fd.readlines()
                )

        errors = []
        for root, dirs, files in os.walk(str(path)):
            # Ignore any directories that we should not walk
            rootpath = str(pathlib.Path(root))
            if any([rootpath.startswith(i) for i in ignoredirs]):
                continue

            for file in files:
                if not (file.endswith(".yaml") or file.endswith(".yml")):
                    continue

                full_path = pathlib.Path(root) / file
                try:
                    doc = list(yaml.safe_load_all(full_path.read_text()))
                except yaml.YAMLError as err:
                    errors.append(f"`{full_path}` failed to parse as yaml: {err}")
                    continue

                for subdoc in doc:
                    if isinstance(subdoc, dict):
                        continue
                    if isinstance(subdoc, list):
                        errors.append(
                            f"`{full_path}` expected dictionary but was list: {subdoc}"
                        )
                        break
                    errors.append(
                        f"`{full_path}` was not a dictionary: {type(subdoc)}: {subdoc}"
                    )

        if errors:
            for error in errors:
                print(f"{FAIL}: {error}")
        else:
            print(OK)
