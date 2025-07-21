"""Module for handling secrets."""

import base64
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from flux_local.manifest import Secret

_LOGGER = logging.getLogger(__name__)


@dataclass
class Auth:
    """Authentication credentials."""

    username: str
    password: str


def get_auth_from_secret(repo_url: str, secret: Secret) -> Auth | None:
    """Return the username and password from the secret.

    This will parse the .dockerconfigjson from the secret and
    find the matching auth for the given repo_url.
    """
    if not secret.string_data:
        raise ValueError(f"Secret {secret.name} does not contain string data")
    if not (docker_config_json := secret.string_data.get(".dockerconfigjson")):
        raise ValueError(f"Secret {secret.name} does not contain .dockerconfigjson")
    try:
        if not (docker_config := json.loads(docker_config_json)):
            return None
    except json.JSONDecodeError as err:
        raise ValueError(
            f"Secret {secret.name} contains invalid .dockerconfigjson {docker_config_json}"
        ) from err

    if not (auths := docker_config.get("auths")):
        _LOGGER.debug("No auths found in secret %s", secret)
        return None

    parsed_url = urlparse(repo_url)
    server_name = parsed_url.netloc
    if not (server_auth := auths.get(server_name)):
        _LOGGER.debug("No auth found for server %s in secret %s", server_name, secret)
        return None

    if not (auth_str := server_auth.get("auth")):
        _LOGGER.debug(
            "No auth string found for server %s in secret %s", server_name, secret
        )
        return None

    decoded_auth = base64.b64decode(auth_str).decode("utf-8")
    username, password = decoded_auth.split(":", 1)
    return Auth(username=username, password=password)
