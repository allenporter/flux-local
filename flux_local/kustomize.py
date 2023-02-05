"""
kustomize.py is a library that can run kustomize build on the local cluster to build a manifest.

Example usage:

```

k = Kustomize()
k.build("./manifests")
```
"""

import subprocess


class Kustomize:
    """Kustomize class that can run kustomize build on the local cluster to build a manifest."""

    def build(self, path):
        """Runs kustomize build on the local cluster to build a manifest."""
        subprocess.run(["kustomize", "build", path])
