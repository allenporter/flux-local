"""Test helpers for flux-local tools."""

import io
import contextlib
import yaml
import pathlib
from typing import Any
from flux_local.command import Command, run
from flux_local.tool.flux_local import _make_parser
from flux_local import git_repo
from flux_local.helm import empty_registry_config_file

FLUX_LOCAL_BIN = "flux-local"

GLOBAL_BUILDER = git_repo.CachableBuilder()


# One-time yaml setup similar to flux_local/tool/flux_local.py
def str_presenter(dumper: yaml.Dumper, data: Any) -> Any:
    """Represent multi-line yaml strings as you'd expect."""
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|" if data.count("\n") > 0 else None
    )


yaml.add_representer(str, str_presenter)
yaml.add_representer(
    pathlib.PosixPath,
    lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", str(data)),
)
try:
    yaml.Loader.yaml_implicit_resolvers.pop("=")
except KeyError:
    pass


async def run_command(args: list[str], env: dict[str, str] | None = None) -> str:
    """Run the flux-local command in-process for performance."""
    if env is not None:
        return await run(Command([FLUX_LOCAL_BIN] + args, env=env))

    parser = _make_parser()
    parsed_args = parser.parse_args(args)
    action = parsed_args.cls()
    with io.StringIO() as buf, contextlib.redirect_stdout(buf):
        with empty_registry_config_file():
            await action.run(builder=GLOBAL_BUILDER, **vars(parsed_args))
        return buf.getvalue()
