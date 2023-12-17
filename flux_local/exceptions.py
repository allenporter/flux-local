"""Exceptions related to flux-local."""

__all__ = [
    "FluxException",
    "InputException",
    "CommandException",
]


class FluxException(Exception):
    """Generic base exception used for this library."""


class InputException(FluxException):
    """Raised when the input files or values are not formatted as expected."""


class CommandException(FluxException):
    """Raised when there is a failure running a subcommand."""


class KustomizeException(CommandException):
    """Raised when there is a failure running a kustomize command."""


class KustomizePathException(CommandException):
    """Raised a Kustomization points to a path that does not exist."""


class HelmException(CommandException):
    """Raised when there is a failure running a helm command."""


class KyvernoException(CommandException):
    """Raised when there is an error running kyverno policy command."""
