flux-local is a set of tools and libraries for managing a local flux gitops repository focused on validation steps to help improve quality of commits, PRs, and general local testing.

This library uses command line tools like kustomize and helm to replicate the behavior of
flux to gather objects in the cluster. It only looks at the local git repo, and not a live
cluster. However, this is fine since the local repository has enough information and the
definition is simple. Secrets are ignored as the content is not needed to validate the
cluster is creating valid objects.

This library at first glance is little more than shell scripts running commands, but is easier
to test, maintain, and evolve. This does not support all features of flux, but should
be close enough for home use.

See [documentation](https://allenporter.github.io/flux-local/) for full quickstart and API reference.
See the [github project](https://github.com/allenporter/flux-local).

## flux-local CLI

The CLI is written in python and packaged as part of the `flux-local` python library, which can be installed using pip and uv. If you have not yet embraced python virtual environments, now might be
the right time to do so to avoid clobbering the packages in your system.

```bash
$ uv venv
$ source .venv/bin/activate
$ uv pip install flux-local
```

### flux-local get

You can use the `flux-local` cli to inspect objects in the cluster, similar to how you might
use the flux command on a real cluster.

This example lists all Kustomizations in the cluster:
```bash
$ flux-local get ks -o wide
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

### flux-local build

You can use the `flux-local` cli to build objects in a cluster, similar to how you
use `kustomize build`, which is used underneath. Here is an example to build all flux
`Kustomization` objects within a git repository, using `kustomize cfg count` to parse
the yaml output:

```bash
$ flux-local build ks --path tests/testdata/cluster/ | kustomize cfg count
Certificate: 2
ClusterPolicy: 1
ConfigMap: 2
GitRepository: 1
HelmRelease: 3
HelmRepository: 3
Kustomization: 4
Namespace: 1
```

Additionally, you can inflate `HelmRelease` objects inside a `Kustomization`. This example
again shows `kustomize cfg count` to parse the yaml output of an inflated `HelmRelease`
objects defined in the cluster:

```bash
$ flux-local build hr podinfo -n podinfo --path tests/testdata/cluster/ | kustomize cfg count
ConfigMap: 1
Deployment: 2
Ingress: 1
Service: 2
```

### flux-local diff

You may also use `flux-local` to verify your local changes to cluster resources have the desird
effect. This is similar to `flux diff` but entirely local. This will run a local `kustomize build`
first against the local repo then again against a prior repo revision, then prints the output:
```diff
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

```diff
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

You may also use an external diff program such as [dyff](https://github.com/homeport/dyff) which
is more compact for diffing yaml resources:
```bash
$ git status
On branch dev
Your branch is up to date with 'origin/dev'.

Changes not staged for commit:
	modified:   home/dev/hajimari-values.yaml

$ export DIFF="dyff between --omit-header --color on"
$ flux-local diff ks home --path clusters/dev/

spec.chart.spec.version  (HelmRelease/hajimari/hajimari)
  ± value change
    - 2.0.2
    + 2.0.1

$ flux-local diff hr hajimari -n hajimari --path clusters/dev/

metadata.labels.helm.sh/chart  (ClusterRoleBinding/default/hajimari)
  ± value change
    - hajimari-2.0.2
    + hajimari-2.0.1

metadata.labels.helm.sh/chart  (PersistentVolumeClaim/default/hajimari-data)
  ± value change
    - hajimari-2.0.2
    + hajimari-2.0.1
```


### flux-local test

You can verify that the resources in the cluster are formatted properly before commit or as part
of a CI system. The `flux-local test` command will build the `Kustomization` resources in the
cluster:

```
$ flux-local test
============================================= test session starts =============================================
collected 18 items

clusters/dev .........                                                                                  [ 50%]
clusters/prod .........                                                                                 [100%]

============================================= 18 passed in 11.43s =============================================
$ flux-local test -v
============================================= test session starts =============================================
collected 18 items

./clusters/dev::certmanager::kustomization PASSED                                                       [  5%]
./clusters/dev::crds::kustomization PASSED                                                              [ 11%]
./clusters/dev::games::kustomization PASSED                                                             [ 16%]
./clusters/dev::home::kustomization PASSED                                                              [ 22%]
./clusters/dev::infrastructure::kustomization PASSED                                                    [ 27%]
./clusters/dev::monitoring::kustomization PASSED                                                        [ 33%]
./clusters/dev::network::kustomization PASSED                                                           [ 38%]
./clusters/dev::services::kustomization PASSED                                                          [ 44%]
./clusters/dev::settings::kustomization PASSED                                                          [ 50%]
./clusters/prod::certmanager::kustomization PASSED                                                      [ 55%]
./clusters/prod::crds::kustomization PASSED                                                             [ 61%]
./clusters/prod::games::kustomization PASSED                                                            [ 66%]
./clusters/prod::home::kustomization PASSED                                                             [ 72%]
./clusters/prod::infrastructure::kustomization PASSED                                                   [ 77%]
./clusters/prod::monitoring::kustomization PASSED                                                       [ 83%]
./clusters/prod::network::kustomization PASSED                                                          [ 88%]
./clusters/prod::services::kustomization PASSED                                                         [ 94%]
./clusters/prod::settings::kustomization PASSED                                                         [100%]

============================================= 18 passed in 11.81s ============================================
```

You may also validate `HelmRelease` objects can be templated properly with the `--enable-helm`
flag. This will run `kustomize build` then run `helm template` on all the `HelmRelease` objects
found.

## GitHub Action

You may use `flux-local` as a github action to verify the health of the cluster on changes
or PRs. The actions expect to find the `flux` and `kustomize` binaries installed.

### test action

The `test` action will validate the cluster will build, and can optionally
validate flux `HelmRelease` builds.

This example will run `flux-local test` against the cluster in `clusters/prod` with
helm release expansion enabled.

```yaml
- name: Setup Flux CLI
  uses: fluxcd/flux2/action@v2.2.2
- uses: allenporter/flux-local/action/test@4.3.1
  with:
    path: clusters/prod
    enable-helm: true
```

### diff action

The `diff` action will show you the final diffs of `Kustomization` or `HelmRelease`
objects that are fully built. While typically you can just read diffs to understand
how kustomzations may be affected, this action also supports overlays and multiple
clusters showing you the final output.

This is an example that diffs a `HelmRelease`:

```yaml
- name: Setup Flux CLI
  uses: fluxcd/flux2/action@v2.2.3
- uses: allenporter/flux-local/action/diff@4.3.1
  id: diff
  with:
    live-branch: main
    path: clusters/prod
    resource: helmrelease
- name: PR Comments
  uses: mshick/add-pr-comment@v2
  if: ${{ steps.diff.outputs.diff != '' }}
  with:
    repo-token: ${{ secrets.GITHUB_TOKEN }}
    message-failure: Unable to post diff
    message: |
      `````diff
      ${{ steps.diff.outputs.diff }}
      `````
```

This is an example of a workflow that will diff `Kustomization` and `HelmRelease` objects
in a repo with multiple clusters (`dev` and `prod`):

```yaml
jobs:
  diffs:
    name: Compute diffs
    runs-on: ubuntu-latest
    strategy:
      matrix:
        cluster_path:
          - clusters/dev
          - clusters/prod
        resource:
          - helmrelease
          - kustomization
    steps:
      - name: Setup Flux CLI
        uses: fluxcd/flux2/action@v2.2.3
      - uses: allenporter/flux-local/action/diff@4.3.1
        id: diff
        with:
          live-branch: main
          path: ${{ matrix.cluster_path }}
          resource: ${{ matrix.resource }}
      - name: PR Comments
        uses: mshick/add-pr-comment@v2
        if: ${{ steps.diff.outputs.diff != '' }}
        with:
          repo-token: ${{ secrets.GITHUB_TOKEN }}
          message-id: ${{ github.event.pull_request.number }}/${{ matrix.cluster_path }}/${{ matrix.resource }}
          message-failure: Unable to post kustomization diff
          message: |
            `````diff
            ${{ steps.diff.outputs.diff }}
            `````
```

## Library

The `flux_local` [library documentation](https://allenporter.github.io/flux-local/) for details
on the python APIs provided.
