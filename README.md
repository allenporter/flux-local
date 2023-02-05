# flux-local

flux-local is a set of tools and libraries for managing a local flux gitops repository focused on validation steps to help improve quality of commits, PRs, and general local testing.

Flux local uses local command line tools like kustomize and helm to replicate the behavior of
flux and understand the desired end state of the cluster. It only looks at the local git repo,
and not a live cluster. However, this is fine since the local repository has enough information
and the definition is simple. Secrets are ignored as the content is not needed to validate the
cluster is creating valid objects.
