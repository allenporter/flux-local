flux-local is a set of tools and libraries for managing a local flux gitops repository focused on validation steps to help improve quality of commits, PRs, and general local testing.

This library uses command line tools like kustomize and helm to replicate the behavior of
flux to gather objects in the cluster. It only looks at the local git repo, and not a live
cluster. However, this is fine since the local repository has enough information and the
definition is simple. Secrets are ignored as the content is not needed to validate the
cluster is creating valid objects.

This library at first glance is little more than shell scripts running commands, but is easier
to test, maintain, and evolve.

See [documentation](https://allenporter.github.io/flux-local/) for full quickstart and API reference.
See the [github project](https://github.com/allenporter/flux-local).

## flux-local CLI

The CLI is written in python and packaged as part of the `flux-local` python library, which can be installed using pip:

```bash
$ pip3 install flux-local
```

You can use the `flux-local` cli to inspect objects in the cluster, similar to how you might
use the flux command on a real cluster.

This example lists all Kustomizations in the cluster:
```bash
$ flux-local get ks
NAME                 PATH                                                   HELMREPOS    RELEASES
apps                 ./tests/testdata/cluster/apps/prod                     0            0
infra-controllers    ./tests/testdata/cluster/infrastructure/controllers    0            0
infra-configs        ./tests/testdata/cluster/infrastructure/configs        2            0
```

This example lists all HelmReleases in the cluster:
```bash
$ flux-local get hr -A
NAMESPACE    NAME       REVISION    CHART              SOURCE
podinfo      podinfo    6.3.2       podinfo-podinfo    podinfo
metallb      metallb    4.1.14      metallb-metallb    bitnami
```

This example lists all HelmReleases in a specific namespace:
```bash
$ flux-local get hr -n metallb
NAME       REVISION    CHART              SOURCE
metallb    4.1.14      metallb-metallb    bitnami
```

You can use the `flux-local` cli to build all objects in a cluster, similar to how you
use `kustomize build`, which is used underneath. Here is an example to build all flux
`Kustomization` objects within a git repository, which will then build all resources within those:

```bash
$ flux-local build clusters/prod/
```

You can also specify the root to build all clusters.

Additionally, you can inflate `HelmRelease` objects inside each `Kustomization` by adding
the `--enable-helm` command line flag:

```bash
$ flux-local build clusters/prod/ --enable-helm
```

You may also use `flux-local` to verify your local changes to cluster resources have the desird
effect. This is similar to `flux diff` but entirely local. This will run a local `kustomize build`
first against the local repo then again against a prior repo revision, then prints the output:
```bash
$ flux-local diff ks apps
---

+++

@@ -2,6 +2,13 @@

   kind: Namespace
   metadata:
     name: podinfo
+- apiVersion: v1
+  data:
+    foo: bar
+  kind: ConfigMap
+  metadata:
+    name: podinfo-config
+    namespace: podinfo
 - apiVersion: helm.toolkit.fluxcd.io/v2beta1
   kind: HelmRelease
   metadata:

```

Additionally `flux-local` can inflate a `HelmRelease` locally and show diffs in the output
objects. This is similar to `flux diff` but for HelmReleases:

```bash
$ flux-local diff hr -n podinfo podinfo
---

+++

@@ -33,8 +33,8 @@

     labels:
       app.kubernetes.io/managed-by: Helm
       app.kubernetes.io/name: podinfo
-      app.kubernetes.io/version: 6.3.2
-      helm.sh/chart: podinfo-6.3.2
+      app.kubernetes.io/version: 6.3.3
+      helm.sh/chart: podinfo-6.3.3
     name: podinfo
   spec:
     ports:
...
```

## Library

The `flux_local` [library documentation](https://allenporter.github.io/flux-local/) for details
on the python APIs provided.
