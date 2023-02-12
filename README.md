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

You may also use `flux-local` to verify your local changes to helm charts have the desired
effect on resources within the `HelmRelease`:

```bash
$ flux-local diff clusters/prod/ --enable-helm
```

## Library

The `flux_local` [library documentation](https://allenporter.github.io/flux-local/) for details
on the python APIs provided.
