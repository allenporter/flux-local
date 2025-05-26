"""Command line tool for building, diffing, validating flux local repositories."""

import argparse
import asyncio
import logging
import sys
import traceback
from typing import Any

import yaml

from flux_local.exceptions import FluxException
from flux_local.helm import empty_registry_config_file
from . import build, diff, get, test, diagnostics, shell

_LOGGER = logging.getLogger(__name__)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Command line utility for inspecting a local flux repository.",
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )

    subparsers = parser.add_subparsers(dest="command", help="Command", required=True)

    build.BuildAction.register(subparsers)
    get.GetAction.register(subparsers)
    diff.DiffAction.register(subparsers)
    test.TestAction.register(subparsers)
    diagnostics.DiagnosticsAction.register(subparsers)
    shell.ShellAction.register(subparsers)
    return parser


def main() -> None:
    """Flux-local command line tool main entry point."""

    def str_presenter(dumper: yaml.Dumper, data: Any) -> Any:
        """Represent multi-line yaml strings as you'd expect.

        See https://github.com/yaml/pyyaml/issues/240
        """
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style="|" if data.count("\n") > 0 else None
        )

    yaml.add_representer(str, str_presenter)

    # https://github.com/yaml/pyyaml/issues/89
    yaml.Loader.yaml_implicit_resolvers.pop("=")

    parser = _make_parser()
    args = parser.parse_args()

    if args.log_level:
        logging.basicConfig(level=args.log_level)

    action = args.cls()
    try:
        with empty_registry_config_file():
            asyncio.get_event_loop().run_until_complete(action.run(**vars(args)))
    except FluxException as err:
        if args.log_level == "DEBUG":
            traceback.print_exc(file=sys.stderr)
        print("flux-local error: ", err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
