"""Flux-local interactive shell implementation."""

import cmd
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass, field, asdict
import logging
from io import TextIOWrapper
from pathlib import Path
import sys
from typing import Any, cast, TextIO

from flux_local.store import Store, Artifact
from flux_local.manifest import (
    NamedResource,
)
from flux_local.tool.format import JsonFormatter, PrintFormatter, YamlFormatter


_LOGGER = logging.getLogger(__name__)


@dataclass
class ResourceType:
    """Represents a resource type in the shell."""

    name: str
    kind: str
    aliases: list[str] = field(default_factory=list)

    def matches(self, name: str) -> bool:
        """Check if this type matches the given name or alias."""
        return name.lower() in [self.name.lower(), *[a.lower() for a in self.aliases]]

    @property
    def singular(self) -> str:
        """Return the singular form of the resource type name."""
        return self.name


class FluxShell(cmd.Cmd):
    """Interactive shell for flux-local."""

    intro = "Welcome to the flux-local shell. Type 'help' for help, 'exit' to quit."
    prompt = "flux-local> "

    # Supported resource types
    RESOURCE_TYPES = [
        ResourceType("kustomization", "Kustomization", ["ks", "kustomizations"]),
        ResourceType("helmrelease", "HelmRelease", ["hr", "helmreleases"]),
        ResourceType("helmrepository", "HelmRepository", ["helmrepo", "helmrepos"]),
        ResourceType("gitrepository", "GitRepository", ["git", "gitrepositories"]),
        ResourceType("ocirepository", "OCIRepository", ["oci", "ocirepositories"]),
    ]

    def __init__(
        self,
        store: Store,
        path: str | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        """Initialize the shell with the given store and I/O streams.

        Args:
            store: The store to use for resource operations (required)
            path: Optional path to the directory containing flux manifests
            stdout: Optional stream for stdout (default: sys.stdout)
            stderr: Optional stream for stderr (default: sys.stderr)
        """
        super().__init__(
            stdin=sys.stdin,
            stdout=stdout if stdout is not None else sys.stdout,
        )
        self.store = store
        self.path = path
        self.stderr = stderr if stderr is not None else sys.stderr

    def print_error(self, message: str) -> None:
        """Print an error message to stderr."""
        print(message, file=self.stderr)

    # Store creation has been moved to ShellAction.run() to avoid duplication

    def _get_resource_type(self, name: str) -> ResourceType | None:
        """Get resource type by name or alias."""
        return next((rt for rt in self.RESOURCE_TYPES if rt.matches(name)), None)

    def do_help(self, arg: str) -> None:
        """List available commands with 'help' or detailed help with 'help <cmd>'."""
        if arg:
            # Try to find a matching command
            func = getattr(self, "help_" + arg, None)
            if func:
                func()
            else:
                super().do_help(arg)
        else:
            # List all commands
            print("Documented commands (type help <topic>):\n", file=self.stdout)
            cmds = []
            for name in self.get_names():
                if name.startswith("do_"):
                    cmd = name[3:]
                    if cmd != "help":
                        cmds.append(cmd)
            print("  " + "  ".join(sorted(cmds)) + "\n", file=self.stdout)

    def do_get(self, arg: str) -> None:
        """Get resources.

        Examples:
            get kustomizations
            get kustomization my-app -n flux-system
            get helmreleases
            get helmrelease my-release -n my-namespace
            get helmrepositories
            get helmrepo my-repo -n flux-system
            get hr --all-namespaces
        """
        try:
            if not arg:
                self.help_get()
                return

            args = self._parse_get_args(arg)
            if not args:
                return

            # Handle resource type aliases
            resource_type = self._get_resource_type(args.resource_type)
            if not resource_type:
                self.print_error(f"Unknown resource type: {args.resource_type}")
                return

            # Get resources
            resources = self._get_resources(
                resource_type.singular,
                namespace=args.namespace,
                name=args.name,
            )

            # Print results
            if not resources:
                print("No resources found", file=self.stdout)
                return

            if args.output:
                self._print_resources(resources, args.output)
            else:
                self._print_resource_table(resources, resource_type.singular)

        except SystemExit:
            # Handle argparse exit
            return
        except Exception as e:
            self.print_error(f"Error: {e}")

    def do_describe(self, arg: str) -> None:
        """Show detailed information about a specific resource.

        Examples:
            describe kustomization my-app -n flux-system
            describe helmrelease my-release -n my-namespace
        """
        try:
            if not arg:
                print(
                    "Usage: describe <resource-type> <name> [-n <namespace>]",
                    file=self.stdout,
                )
                return

            args = self._parse_get_args(arg)
            if not args or not args.resource_type or not args.name:
                print(
                    "Usage: describe <resource-type> <name> [-n <namespace>]",
                    file=self.stdout,
                )
                return

            # Handle resource type aliases
            resource_type = self._get_resource_type(args.resource_type)
            if not resource_type:
                self.print_error(f"Unknown resource type: {args.resource_type}")
                return

            # Get resources
            resources = self._get_resources(
                resource_type.singular,
                namespace=args.namespace or "default",
                name=args.name,
            )

            if not resources:
                print(
                    f"No {resource_type.singular} found with name {args.name}",
                    file=self.stdout,
                )
                return

            resource = resources[0]
            self._print_resource_details(resource)

        except Exception as e:
            self.print_error(f"Error describing resource: {e}")

    def complete_describe(
        self, text: str, line: str, begidx: int, endidx: int
    ) -> list[str]:
        """Provide tab completion for the describe command."""
        return self.complete_get(text, line, begidx, endidx)

    def complete_get(self, text: str, line: str, begidx: int, endidx: int) -> list[str]:
        """Provide tab completion for the get command."""
        if not text:
            return [rt.name for rt in self.RESOURCE_TYPES]
        return [
            rt.name for rt in self.RESOURCE_TYPES if rt.name.startswith(text.lower())
        ]

    def _parse_get_args(self, arg: str) -> Namespace | None:
        """Parse arguments for the get command."""
        parser = ArgumentParser(prog="get", add_help=False)
        parser.add_argument(
            "resource_type", help="Resource type (e.g., kustomization, helmrelease)"
        )
        parser.add_argument("name", nargs="?", help="Resource name")
        parser.add_argument("-n", "--namespace", help="Namespace")
        parser.add_argument(
            "-A",
            "--all-namespaces",
            action="store_true",
            help="List resources across all namespaces",
        )
        parser.add_argument(
            "-o", "--output", choices=["json", "yaml", "name"], help="Output format"
        )

        try:
            # Handle empty argument case
            if not arg.strip():
                parser.print_help()
                return None

            # Parse the arguments
            args, _ = parser.parse_known_args(arg.split())

            # Handle all-namespaces flag
            if args.all_namespaces:
                args.namespace = None

            return args

        except SystemExit:
            # Handle argparse exit from help or error
            return None

    def _get_resources(
        self,
        resource_type: str,
        namespace: str | None = None,
        name: str | None = None,
    ) -> list[Any]:
        """Get resources of the specified type."""
        # Resolve the resource type to handle aliases
        rt = self._get_resource_type(resource_type)
        if not rt:
            raise ValueError(f"Unknown resource type: {resource_type}")

        # Get all objects of the specified kind
        resources = self.store.list_objects(kind=rt.kind)

        # Filter by namespace if specified
        if namespace is not None:
            resources = [
                r for r in resources if getattr(r, "namespace", None) == namespace
            ]

        # Filter by name if specified
        if name is not None:
            resources = [r for r in resources if getattr(r, "name", "") == name]

        # If we're getting kustomizations, ensure they have the correct path
        if rt.kind == "Kustomization" and hasattr(self, "path"):
            # If we have a path, update the resource path to be relative to it
            for resource in resources:
                if self.path and hasattr(resource, "path") and resource.path:
                    resource_path = Path(resource.path)
                    if not resource_path.is_absolute() and self.path:
                        resource.path = str(Path(self.path) / resource.path)

        return resources

    def _resource_to_dict(self, resource: Any) -> dict[str, Any]:
        """Convert a resource to a dictionary."""
        if hasattr(resource, "to_dict") and callable(resource.to_dict):
            result = resource.to_dict()
            if isinstance(result, dict):
                return result
        if hasattr(resource, "dict") and callable(resource.dict):
            result = resource.dict()
            if isinstance(result, dict):
                return result
        # Fallback to getting all non-callable, non-private attributes
        return {
            k: getattr(resource, k)
            for k in dir(resource)
            if not k.startswith("_") and not callable(getattr(resource, k))
        }

    def _print_resource_table(self, resources: list[Any], resource_type: str) -> None:
        """Print resources in a table format with status information."""
        if not resources:
            print("No resources found", file=self.stdout)
            return

        # Get the first resource to determine the type
        rt = self._get_resource_type(resource_type)
        if not rt:
            self._print_resources(resources, None)
            return

        # Define columns based on resource type
        if rt.kind == "Kustomization":
            headers = ["NAME", "NAMESPACE", "STATUS", "MESSAGE"]
            rows = []
            for r in resources:
                status = self.store.get_status(
                    NamedResource(rt.kind, r.namespace, r.name)
                )
                status_str = str(status.status) if status else "Unknown"
                message = status.error if status and status.error else ""
                rows.append([r.name, r.namespace, status_str, message])
        elif rt.kind == "HelmRelease":
            headers = ["NAME", "NAMESPACE", "CHART", "VERSION", "STATUS"]
            rows = []
            for r in resources:
                status = self.store.get_status(
                    NamedResource(rt.kind, r.namespace, r.name)
                )
                status_str = str(status.status) if status else "Unknown"
                chart_name = r.chart.name if r.chart else "Unknown"
                chart_version = (
                    r.chart.version if r.chart and r.chart.version else "Unknown"
                )
                rows.append(
                    [
                        r.name,
                        r.namespace,
                        f"{chart_name}@{chart_version}",
                        status_str,
                    ]
                )
        elif rt.kind in ["GitRepository", "OCIRepository"]:
            headers = ["NAME", "NAMESPACE", "URL", "STATUS"]
            rows = []
            for r in resources:
                status = self.store.get_status(
                    NamedResource(rt.kind, r.namespace, r.name)
                )
                status_str = str(status.status) if status else "Unknown"
                rows.append(
                    [
                        r.name,
                        r.namespace,
                        getattr(r, "url", getattr(r, "repository", "")),
                        status_str,
                    ]
                )
        else:
            # Default table view for other resource types
            self._print_resources(resources, None)
            return

        # Print the table
        from tabulate import tabulate

        print(
            tabulate(
                rows,
                headers=headers,
                tablefmt="plain",
                maxcolwidths=[None, None, None, 50],
            ),
            file=self.stdout,
        )

    def _print_resource_details(self, resource: Any) -> None:
        """Print detailed information about a resource."""
        from rich.console import Console
        from rich.panel import Panel
        from rich.syntax import Syntax
        import yaml

        console = Console(file=self.stdout)

        # Get status information
        status = self.store.get_status(
            NamedResource(resource.kind, resource.namespace, resource.name)
        )

        # Print basic info
        console.print(
            Panel.fit(
                f"[bold]Name:[/] {resource.name}\n"
                f"[bold]Namespace:[/] {getattr(resource, 'namespace', 'default')}\n"
                f"[bold]Type:[/] {resource.__class__.__name__}\n"
                f"[bold]Status:[/] {status.status if status else 'Unknown'}"
                f"{('\n[bold]Message:[/] ' + status.error) if status and status.error else ''}",
                title="[bold]Resource Information",
            )
        )

        # Print spec as YAML
        spec = {
            k: v
            for k, v in resource.dict().items()
            if k not in ["metadata", "status"] and v is not None
        }
        if spec:
            console.print(
                Panel(
                    Syntax(
                        yaml.dump(spec, sort_keys=False, default_flow_style=False),
                        "yaml",
                        theme="monokai",
                        line_numbers=False,
                    ),
                    title="[bold]Specification",
                )
            )

        # Print status if available
        if status:
            console.print(
                Panel(
                    Syntax(
                        yaml.dump(
                            (
                                {"status": status.status, "error": status.error}
                                if status
                                else {}
                            ),
                            sort_keys=False,
                            default_flow_style=False,
                        ),
                        "yaml",
                        theme="monokai",
                        line_numbers=False,
                    ),
                    title="[bold]Status",
                )
            )

        # Print artifact information if available
        try:
            artifact = self.store.get_artifact(
                NamedResource(resource.kind, resource.namespace, resource.name),
                Artifact,
            )
            if artifact:
                console.print(
                    Panel(
                        Syntax(
                            yaml.dump(
                                asdict(artifact),
                                sort_keys=False,
                                default_flow_style=False,
                            ),
                            "yaml",
                            theme="monokai",
                            line_numbers=False,
                        ),
                        title="[bold]Artifact",
                    )
                )
        except Exception as e:
            _LOGGER.debug("Error getting artifact: %s", e)

    def _print_resources(
        self,
        resources: list[Any],
        output_format: str | None = None,
    ) -> None:
        """Print resources in the specified format.

        Args:
            resources: List of resource objects to print
            output_format: Output format - 'json', 'yaml', 'name', or None for table
        """
        if not resources:
            print("No resources found", file=self.stdout)
            return

        resource_dicts = [self._resource_to_dict(r) for r in resources]

        try:
            # Cast stdout to TextIOWrapper which is compatible with TextIO
            stdout = cast(TextIOWrapper, self.stdout)
            if output_format == "json":
                JsonFormatter().print(resource_dicts, file=stdout)
            elif output_format == "yaml":
                YamlFormatter().print(resource_dicts, file=stdout)
            elif output_format == "name":
                for resource in resources:
                    name = getattr(resource, "name", "")
                    namespace = getattr(resource, "namespace", "")
                    if namespace:
                        print(f"{namespace}/{name}", file=stdout)
                    else:
                        print(name, file=stdout)
            else:
                # Default to table format
                if not resource_dicts:
                    return
                # Use the first dict to get the keys
                keys = list(resource_dicts[0].keys())
                PrintFormatter(keys).print(resource_dicts, stdout)
        except Exception as e:
            self.print_error(f"Error formatting output: {e}")

    def help_get(self) -> None:
        """Show help for the get command."""
        self.print_error(
            "Usage: get <resource-type> [name] [options]\n"
            "Get resources.\n\n"
            "Examples:\n"
            "  get kustomizations\n"
            "  get kustomization my-app -n flux-system\n"
            "  get helmreleases -A\n"
            "  get gitrepositories -o json"
        )

    def do_exit(self, arg: str) -> bool:
        """Exit the shell."""
        print("Exiting flux-local shell", file=self.stdout)
        return True

    def do_quit(self, arg: str) -> bool:
        """Exit the shell (alias for exit)."""
        return self.do_exit(arg)

    def do_EOF(self, arg: str) -> bool:
        """Handle EOF (Ctrl+D) to exit the shell."""
        print("\n", file=self.stdout, end="")
        self.stdout.flush()
        return self.do_exit(arg)
