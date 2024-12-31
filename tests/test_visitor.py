"""Tests for the visitor module."""

from typing import Any
import yaml

import pytest


from flux_local.visitor import strip_resource_attributes

STRIP_ATTRIBUTES = [
    "app.kubernetes.io/version",
    "chart",
]


@pytest.mark.parametrize(
    ("metadata", "expected_metadata"),
    [
        (
            {
                "labels": {
                    "app.kubernetes.io/version": "1.0.0",
                    "app.kubernetes.io/managed-by": "Helm",
                }
            },
            {
                "labels": {
                    "app.kubernetes.io/managed-by": "Helm",
                },
            },
        ),
        (
            {
                "annotations": {
                    "app.kubernetes.io/version": "1.0.0",
                    "app.kubernetes.io/managed-by": "Helm",
                }
            },
            {
                "annotations": {
                    "app.kubernetes.io/managed-by": "Helm",
                },
            },
        ),
        (
            {},
            {},
        ),
    ],
)
def test_strip_resource_attributes(
    metadata: dict[str, Any], expected_metadata: dict[str, Any]
) -> None:
    """Test the strip_resource_attributes function."""
    resource = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "my-configmap",
            "namespace": "default",
            **metadata,
        },
        "data": {
            "key1": "value1",
            "key2": "value2",
        },
    }
    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert resource == {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "my-configmap",
            "namespace": "default",
            **expected_metadata,
        },
        "data": {
            "key1": "value1",
            "key2": "value2",
        },
    }


def test_strip_deployment_metadata() -> None:
    """Test the strip_resource_attributes function."""
    resource = yaml.load(
        """apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-deployment
  labels:
    app: nginx
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
        app.kubernetes.io/version: 1.0.0
    spec:
      containers:
      - name: nginx
        image: nginx:1.14.2
        ports:
        - containerPort: 80
""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-deployment
  labels:
    app: nginx
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
      - name: nginx
        image: nginx:1.14.2
        ports:
        - containerPort: 80
"""
    )


def test_strip_list_metadata() -> None:
    """Test the stripping metadata from a list resource."""
    resource = yaml.load(
        """apiVersion: v1
items:
- apiVersion: stable.example.com/v1
  kind: CronTab
  metadata:
    annotations:
      app: my-cron-tab
      app.kubernetes.io/version: 1.0.0
    creationTimestamp: '2021-06-20T07:35:27Z'
    generation: 1
    name: my-new-cron-object
    namespace: default
    resourceVersion: '1326'
    uid: 9aab1d66-628e-41bb-a422-57b8b3b1f5a9
  spec:
    cronSpec: '* * * * */5'
    image: my-awesome-cron-image
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''

""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: v1
items:
- apiVersion: stable.example.com/v1
  kind: CronTab
  metadata:
    annotations:
      app: my-cron-tab
    creationTimestamp: '2021-06-20T07:35:27Z'
    generation: 1
    name: my-new-cron-object
    namespace: default
    resourceVersion: '1326'
    uid: 9aab1d66-628e-41bb-a422-57b8b3b1f5a9
  spec:
    cronSpec: '* * * * */5'
    image: my-awesome-cron-image
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
"""
    )


def test_strip_list_null_items() -> None:
    """Test corner cases of handling metadata."""
    resource = yaml.load(
        """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items:

""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items: null
"""
    )


def test_strip_list_item_without_metdata() -> None:
    """Test corner cases of handling metadata."""
    resource = yaml.load(
        """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items:
- kind: CronTab
""",
        Loader=yaml.Loader,
    )

    strip_resource_attributes(resource, STRIP_ATTRIBUTES)
    assert (
        yaml.dump(resource, sort_keys=False)
        == """apiVersion: v1
kind: List
metadata:
  resourceVersion: ''
  selfLink: ''
items:
- kind: CronTab
"""
    )
