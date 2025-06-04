"""Module for working with kubernetes values."""

import base64
from collections.abc import Iterable, Generator, Callable
import logging
from typing import TypeVar, Any
import re

import yaml

from .manifest import (
    HelmRelease,
    SECRET_KIND,
    Kustomization,
    CONFIG_MAP_KIND,
    ConfigMap,
    Secret,
    VALUE_PLACEHOLDER_TEMPLATE,
    ValuesReference,
)
from .exceptions import HelmException, InputException, InvalidValuesReference


_LOGGER = logging.getLogger(__name__)


_T = TypeVar("_T", bound=ConfigMap | Secret)


class ClusterConfig:
    """Interface for accessing all cluster configuration."""

    def __init__(
        self,
        secrets: Callable[[], Iterable[Secret]],
        config_maps: Callable[[], Iterable[ConfigMap]],
    ) -> None:
        """Initialize ClusterConfig."""
        self._secrets = secrets
        self._config_maps = config_maps

    @property
    def secrets(self) -> Iterable[Secret]:
        """Available Secret objects in the cluster."""
        return iter(self._secrets())

    @property
    def config_maps(self) -> Iterable[ConfigMap]:
        """Available ConfigMap objects in the cluster."""
        return iter(self._config_maps())


def cluster_config(
    secrets: list[Secret], config_maps: list[ConfigMap]
) -> ClusterConfig:
    """Create a ClusterConfig from a list of secrets and configmaps."""
    return ClusterConfig(
        lambda: secrets,
        lambda: config_maps,
    )


def merge_cluster_config(
    config: ClusterConfig, secrets: list[Secret], config_maps: list[ConfigMap]
) -> ClusterConfig:
    """Create a ClusterConfig from a list of secrets and configmaps."""
    return ClusterConfig(
        lambda: list(config.secrets) + secrets,
        lambda: list(config.config_maps) + config_maps,
    )


def ks_cluster_config(kustomizations: list[Kustomization]) -> ClusterConfig:
    """Create a ClusterConfig from a list of Kustomizations."""

    def secrets_iter() -> Generator[Secret, None, None]:
        for ks in kustomizations:
            for secret in ks.secrets:
                yield secret

    def config_maps_iter() -> Generator[ConfigMap, None, None]:
        for ks in kustomizations:
            for config_map in ks.config_maps:
                yield config_map

    return ClusterConfig(
        secrets_iter,
        config_maps_iter,
    )


def _find_object(name: str, namespace: str, objects: Iterable[_T]) -> _T | None:
    """Find the object in the list of objects."""
    for obj in objects:
        if obj.name == name and obj.namespace == namespace:
            return obj
    return None


def _decode_config_or_secret_value(
    name: str, string_data: dict[str, str] | None, binary_data: dict[str, str] | None
) -> dict[str, str] | None:
    """Return the config or secret data."""
    if binary_data:
        try:
            return {
                k: base64.b64decode(v).decode("utf-8") for k, v in binary_data.items()
            }
        except ValueError:
            raise HelmException(f"Unable to decode binary data for configmap {name}")
    return string_data


def _get_secret_data(
    name: str, namespace: str, secrets: Iterable[Secret]
) -> dict[str, str] | None:
    """Find the secret value in the kustomization."""
    found: Secret | None = _find_object(name, namespace, secrets)
    if not found:
        return None
    return _decode_config_or_secret_value(
        f"{namespace}/{name}", found.string_data, found.data
    )


def _get_configmap_data(
    name: str, namespace: str, config_maps: Iterable[ConfigMap]
) -> dict[str, str] | None:
    """Find the configmap value in the kustomization."""
    found: ConfigMap | None = _find_object(name, namespace, config_maps)
    if not found:
        return None
    return _decode_config_or_secret_value(
        f"{namespace}/{name}", found.data, found.binary_data
    )


def _lookup_value_reference(
    ref: ValuesReference,
    namespace: str,
    cluster_config: ClusterConfig,
) -> str | None:
    _LOGGER.debug("Expanding value reference %s", ref)
    found_data: dict[str, str] | None = None
    if ref.kind == SECRET_KIND:
        found_data = _get_secret_data(ref.name, namespace, cluster_config.secrets)
    elif ref.kind == CONFIG_MAP_KIND:
        found_data = _get_configmap_data(
            ref.name, namespace, cluster_config.config_maps
        )
    else:
        raise InvalidValuesReference(f"Unsupported valueFrom kind {ref.kind}")

    found_value: str | None = None
    if found_data is None:
        if not ref.optional:
            _LOGGER.warning(
                "Unable to find %s %s/%s referenced",
                ref.kind,
                namespace,
                ref.name,
            )
        if ref.target_path:
            # When a target path is specified, the value is expected to be
            # a simple value type. Create a synthetic placeholder value, otherwise
            # there is nothing to replace.
            return VALUE_PLACEHOLDER_TEMPLATE.format(name=ref.name)
        return None

    elif (found_value := found_data.get(ref.values_key)) is None:
        raise InvalidValuesReference(
            f"Unable to find key {ref.values_key} in {namespace}/{ref.name}"
        )

    return found_value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge two dictionaries, similar to how Helm merges values. Lists are replaced entirely (Helm behavior)."""
    result = base.copy()
    for key, override_value in override.items():
        base_value = result.get(key)
        if (
            base_value is not None
            and isinstance(base_value, dict)
            and isinstance(override_value, dict)
        ):
            result[key] = _deep_merge(base_value, override_value)
        else:
            result[key] = override_value
    return result


def _update_helmrelease_values(
    ref: ValuesReference,
    found_value: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Expand value references in the HelmRelease."""

    if ref.target_path:

        raw_parts = re.split(r"(?<!\\)\.", ref.target_path)
        parts = [re.sub(r"\\(.)", r"\1", raw_part) for raw_part in raw_parts]

        inner_values = values
        for part in parts[:-1]:
            if part not in inner_values:
                inner_values[part] = {}
            elif not isinstance(inner_values[part], dict):
                raise InputException(
                    f"Expected '{ref.name}' field '{ref.target_path}' values to be a dict, found {type(inner_values[part])}"
                )
            inner_values = inner_values[part]

        inner_values[parts[-1]] = found_value
    else:
        obj = yaml.load(found_value, Loader=yaml.SafeLoader)
        # Handle empty YAML file case
        if obj is None:
            obj = {}
        if not isinstance(obj, dict):
            raise InputException(
                f"Expected '{ref.name}' field '{ref.target_path}' values to be valid yaml, found {type(values)}"
            )
        values = _deep_merge(values, obj)

    return values


def expand_value_references(
    helm_release: HelmRelease, kustomization: Kustomization
) -> HelmRelease:
    if not helm_release.values_from:
        return helm_release

    values: dict[str, Any] = {}
    cluster_config = ks_cluster_config([kustomization])
    for ref in helm_release.values_from:
        _LOGGER.debug("Expanding value reference %s", ref)

        try:
            found_value = _lookup_value_reference(
                ref,
                helm_release.namespace,
                cluster_config,
            )
        except InvalidValuesReference as err:
            _LOGGER.warning(
                "Skipping ValuesReference for %s: %s", helm_release.namespaced_name, err
            )
            continue

        if found_value is None:
            continue

        try:
            values = _update_helmrelease_values(ref, found_value, values)
        except InputException as err:
            raise HelmException(
                f"Error building HelmRelease '{helm_release.namespaced_name}': {err}"
            )

    if helm_release.values:
        values = _deep_merge(values, helm_release.values)

    helm_release.values = values
    return helm_release


def expand_postbuild_substitute_reference(
    ks: Kustomization, cluster_config: ClusterConfig
) -> Kustomization:
    """Expand postbuild substituteFrom references to substitutions."""
    if not ks.postbuild_substitute_from:
        return ks

    values: dict[str, Any] = ks.postbuild_substitute or {}
    for ref in ks.postbuild_substitute_from:
        _LOGGER.debug("Expanding substitute reference %s", ref)
        if not ks.namespace:
            raise InvalidValuesReference(
                "Kustomization with substitueFrom has no namespace"
            )
        found_data: dict[str, str] | None = None
        if ref.kind == SECRET_KIND:
            found_data = _get_secret_data(
                ref.name, ks.namespace, cluster_config.secrets
            )
        elif ref.kind == CONFIG_MAP_KIND:
            found_data = _get_configmap_data(
                ref.name, ks.namespace, cluster_config.config_maps
            )
        else:
            _LOGGER.warning(
                "Skipping SubstituteReference for %s: %s", ks.namespaced_name, ref.name
            )
            continue

        if found_data is None:
            if (
                not ref.optional and not ref.kind == SECRET_KIND
            ):  # Secrets are commonly filtered
                _LOGGER.warning(
                    "Unable to find SubstituteReference for %s: %s",
                    ks.namespaced_name,
                    ref.name,
                )
            continue

        values.update(found_data)
    _LOGGER.debug("update_postbuild_substitutions=%s", values)
    ks.update_postbuild_substitutions(values)
    return ks
