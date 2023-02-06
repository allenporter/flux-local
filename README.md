flux-local is a set of tools and libraries for managing a local flux gitops repository focused on validation steps to help improve quality of commits, PRs, and general local testing.

This library uses command line tools like kustomize and helm to replicate the behavior of
flux to gather objects in the cluster. It only looks at the local git repo, and not a live
cluster. However, this is fine since the local repository has enough information and the
definition is simple. Secrets are ignored as the content is not needed to validate the
cluster is creating valid objects.

This library at first glance is little more than shell scripts running commands, but is easier
to test, maintain, and evolve.

See [documentation](https://allenporter.github.io/ical/) for full quickstart and API reference.
See the [github project](https://github.com/allenporter/flux-local).
