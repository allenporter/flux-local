"""Library for running `helm template` to produce local items in the cluster."""

import subprocess


def render(chart, values):
    """Runs `helm template` to produce local items in the cluster."""
    cmd = ["helm", "template", chart, "--values", values]
    return subprocess.check_output(cmd)
