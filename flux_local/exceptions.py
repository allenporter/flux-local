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


class KustomizePathException(KustomizeException):
    """Raised a Kustomization points to a path that does not exist."""


class ResourceFailedError(FluxException):
    """Raised when a resource reconciliation has failed and is in a terminal state."""

    def __init__(self, resource_name: str, message: str | None) -> None:
        super().__init__(
            f"Resource {resource_name} failed: {message or 'Unknown error'}"
        )
        self.resource_name = resource_name
        self.message = message


class DependencyFailedError(InputException):
    """Raised when a Kustomization dependency has failed."""

    def __init__(
        self,
        kustomization_id: str,
        dependency_id: str,
        dependency_error: str | None,
    ):
        self.kustomization_id = kustomization_id
        self.dependency_id = dependency_id
        self.dependency_error = dependency_error
        super().__init__(
            f"Kustomization {kustomization_id} dependency {dependency_id} failed: "
            f"{dependency_error or 'Unknown error'}"
        )


class ObjectNotFoundError(FluxException):
    """Raised when an object is not found in the store."""


class HelmException(CommandException):
    """Raised when there is a failure running a helm command."""


class InvalidValuesReference(FluxException):
    """Exception raised for an unsupported ValuesReference."""


class InvalidSubstituteReference(FluxException):
    """Exception raised for an unsupported SubstituteReference."""
