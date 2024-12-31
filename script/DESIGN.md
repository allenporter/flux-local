# flux-local v5

## Introduction

In v4, the major overhaul was to move use `flux build` as the primary way to
build fluxtomizations which simplified some of the structure. Now that the
code supports a bunch of features, it seems useful to try to rewrite to match
the next set of needs.

## Goals

Here are some goals for v5:

- Increase parallelism: For example, operations that need helm templating
  currently don't start until all other helm operations have finished. This
  may be able to happen if we can use `dependsOn` to ensure all pre-requisites
  are built (e.g. a `HelmRepository` may be from another fluxtomization).
- Unnecessary kustomization building: When building a single kustomization,
  we don't necessarily need to build the entire world. Stopping early seems
  like a nice feature and could use `dependsOn` to understand dependencies
  to ensure building.
- Shared code patterns when parsing output of helm templates and outputs of
  kustomize build for stuff like image version extraction.
- Move off of pydantic v1 API since it is deprecated. 
- `flux-local build` is kind of a mess? It seems like it needs to be rethought.
- Caching is somewhat brittle, and should probably include the skip crds/secrets
  flags, or just remove the need overall for caching of specific commands.
- Improved unit testing may be possible if we can pull more of the code out of
  CLI commands into reusable libraries.
  

## Background

Here is some background on what happens today for the first initial step:
- Search for any kustomization with `kustomize cfg grep kind=Kustomization` in
  order to bootstrap. The idea here is to support minimal configuration input,
  and the first pass at searching for a fluxtomization.
- All kustomizations found are added to a queue with some amount of path correction.
- For every kustomization found, call `flux build` on it. It is currently drained
  all at once and run in parallel.
- Any additional kustomization found is added to the queue and the process of
  building is repeated until there are no more.
- All results from each build step above is only looking at Kustomizations and
  dropping all other results (though they are cached for later)

The second step is to then process the results of the cluster by rebuilding
all the kustomizations again. Any CRDs and Secrets are filtered out if needed.
The results from the previous steps are cached with a brittle very specific
cache. Specifically, a few types of resources are collected: `HelmRepository`,
`HelmRelease`, etc. Additional, generic objects are collected
with an object listener that supports arbitrary doc kinds, which in practice
is used for tracking images used. Each of the objects built are associated
with the generated `Kustomization` object.

Then there is another pass that specifically traverses `HelmRelease` and
`HelmRepository` objects that are found and calls the listeners on them to
keep track of them associated with a specific `Kustomization` for diffing.
These are asyncio functions that that are run staged together.

Finally, a command like diff may run another traverisal to call `helm template`
on each `HelmRelease`, recording the output for diffing. The diffing all happens
staged, lining up all staged objects together when they are found on both
sides or when finished and only on one side.

## Initial Design Ideas

The overall design screams for an asynchronous state management system that can
allow custom graphs/nodes to be put together and wired up at different stages.
It still likely makes sense to preserve the overall specific graph of kustomizations
and helm releases, however, there may still be smaller patterns that are reusable.

The `pytransitions` library supports an `AsyncMachine` that may be useful for
representing this problem. It may be useful to start small and try using this
for a sub-problem that is currently somewhat hairy.

## Alternatives

An attactive option may be to rewrite the entire thing on golang so that it can
reuse the actual flux libraries. This is worth exploring in more detail, or
possibly prototyping.
