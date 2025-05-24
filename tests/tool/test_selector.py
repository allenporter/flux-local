import pytest
from pathlib import Path
from flux_local.helm import Options as HelmOptions
from flux_local.tool.selector import build_helm_options

def test_build_helm_options_no_user_config_uses_default():
    # Arrange
    mock_temp_path = Path("/fake/temp/helm_config.json")
    # These are the arguments that would typically be parsed by argparse
    # and then passed as **vars(args) to build_helm_options.
    # For this test, we simulate that 'registry_config' was not provided by the user (so it's None),
    # and 'default_registry_config_path' was set by our logic in flux_local.py.
    kwargs = {
        "skip_crds": True,
        "skip_secrets": True,
        "skip_kinds": None,
        "kube_version": None,
        "api_versions": None,
        "registry_config": None,  # User did not provide --registry-config
        "default_registry_config_path": mock_temp_path, # Set by flux_local.py
    }

    # Act
    helm_opts = build_helm_options(**kwargs)

    # Assert
    assert helm_opts.registry_config == str(mock_temp_path)

def test_build_helm_options_user_config_overrides_default():
    # Arrange
    mock_temp_path = Path("/fake/temp/helm_config.json")
    user_config_path = "/user/path/my_config.yaml"
    # Simulate that the user provided --registry-config, and
    # default_registry_config_path was also set by flux_local.py.
    kwargs = {
        "skip_crds": True,
        "skip_secrets": True,
        "skip_kinds": None,
        "kube_version": None,
        "api_versions": None,
        "registry_config": user_config_path, # User provided --registry-config
        "default_registry_config_path": mock_temp_path, # Set by flux_local.py
    }

    # Act
    helm_opts = build_helm_options(**kwargs)

    # Assert
    assert helm_opts.registry_config == user_config_path

def test_build_helm_options_no_user_config_and_no_default():
    # Arrange
    # Simulate that the user did not provide --registry-config, AND
    # default_registry_config_path was NOT set (e.g., if the context manager failed or was not used).
    # In this case, 'default_registry_config_path' key might be absent from kwargs
    # or explicitly None if args object had it as an attribute with default None.
    # The selector.py logic `kwargs.get("default_registry_config_path")` handles both.
    kwargs_scenario1 = {
        "skip_crds": True,
        "skip_secrets": True,
        "skip_kinds": None,
        "kube_version": None,
        "api_versions": None,
        "registry_config": None, # User did not provide --registry-config
        # "default_registry_config_path" is absent
    }
    kwargs_scenario2 = {
        "skip_crds": True,
        "skip_secrets": True,
        "skip_kinds": None,
        "kube_version": None,
        "api_versions": None,
        "registry_config": None, # User did not provide --registry-config
        "default_registry_config_path": None, # Explicitly None
    }

    # Act & Assert for scenario 1 (key absent)
    helm_opts1 = build_helm_options(**kwargs_scenario1)
    assert helm_opts1.registry_config is None, \
        "registry_config should be None when user config is None and default path is absent"

    # Act & Assert for scenario 2 (key present but value is None)
    helm_opts2 = build_helm_options(**kwargs_scenario2)
    assert helm_opts2.registry_config is None, \
        "registry_config should be None when user config is None and default path is None"
