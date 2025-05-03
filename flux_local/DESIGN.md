# Design

These are some thoughts for a redesign for flux-local based on current problems
with the current internals and next set of features that would be nice to support.
Maybe this can target v8.

## Problems

There are a few problems with the current design:

- External `GitRepository` can not be used as a source for a `Kustomization` or
  `HelmRelease` as these sources are not supported very well in the current
  design.
- Changing around dependencies of the flow is not very easy.
- Flux Operator is not supported at all and introduces new CRDs such as
  as a `ResourceSet`.
- Failures are difficult to debug
- Failures stop the entire process, so you cant' see all failures at once

## Overview

The proposed direction is to redesign to mimic the internals of flux and
make the design work like controllers / operators.

### Controllers

In flux there are a few existing controllers:

- `SourceController` which is a controller that watches `GitRepository` and
  `OCIRepository` objects and updates the local git repo. This controller
  exposes a service that can be used to checkout a specific revision of the
  git repo used by other controllers.
- `KustomizeController` which is a controller that watches `Kustomization`
  objects and updates the local git repo. This controller will query the
  `SourceController` to get the local git repo and then build the kustomization
  and write the objects to kubernetes.
- `HelmReleaseController` which is a controller that watches `HelmRelease`
  objects, builds the helm release, and writes the objects to kubernetes.

### SourceController

The `SourceController` manages artifacts from source Custom Resources like `GitRepository` and `OCIRepository`. Its primary responsibilities include:

- **Fetching Artifacts:** Cloning or fetching sources (e.g., Git repositories) based on the specifications in the CR (URL, branch, tag, commit).
- **Caching:** Storing fetched artifacts locally in a managed cache directory. The cache must support storing **multiple revisions** (e.g., different commit SHAs) of the same source concurrently, likely by incorporating the revision into the cache path (e.g., `/cache/repo/<sha>`). This allows different Kustomizations or HelmReleases to depend on different versions of the same source.
- **Status Updates:** Recording the status of the artifact fetch (e.g., fetched revision, local path, errors) and making this information available.
- **Authentication:** Handling necessary credentials for accessing private sources (details TBD).

#### Interface for Downstream Controllers

The `SourceController` (or the central state store it updates) needs to provide a mechanism for other controllers (`KustomizeController`, `HelmReleaseController`) to retrieve the local filesystem path for a specific revision of a requested source artifact.

When a `Kustomization` or `HelmRelease` specifies a `sourceRef` (e.g., `{ kind: GitRepository, name: my-repo }`), the respective controller will query for the cached artifact associated with `my-repo`. The query needs to resolve to a specific revision (commit SHA) based on the `GitRepository` object's status or potentially a user override. The query result should include:

- **Local Path:** The absolute path to the directory containing the checked-out source code for the **specific revision** in the local cache.
- **Revision:** The specific revision (commit SHA) that was fetched.
- **Status:** Indication of success or failure of the fetch operation.

This interface ensures that build controllers (`KustomizeController`, `HelmReleaseController`) can reliably access the correct source code needed for their operations without needing to manage the fetching logic themselves.

### KustomizeController

The `KustomizeController` is responsible for processing `Kustomization` custom resources and generating the corresponding Kubernetes manifests.

-   **Watches:** `Kustomization` objects.
-   **Inputs:**
    -   The `Kustomization` object definition (`spec.sourceRef`, `spec.path`, `spec.dependsOn`, etc.).
    -   The specific source revision (commit SHA) required for the build (determined externally).
-   **Workflow:**
    1.  **Dependency Check:** Ensure resources listed in `spec.dependsOn` are ready by querying the central state store.
    2.  **Source Retrieval:** Query the `SourceController` interface (providing the `sourceRef` and the explicit target revision SHA) to get the local path to the correct cached source artifact.
    3.  **Build Execution:** Run `kustomize build` targeting the directory identified by the source path and `spec.path`.
    4.  **Status Update:** Record the build result (success/failure, errors) and a reference to the output manifests in the state store associated with the `Kustomization` object.
-   **Outputs:**
    -   Rendered Kubernetes manifests.
    -   Updated status for the `Kustomization` object in the state store.

### HelmReleaseController

The `HelmReleaseController` processes `HelmRelease` custom resources, handling chart retrieval, value resolution, and template rendering.

-   **Watches:** `HelmRelease` objects.
-   **Inputs:**
    -   The `HelmRelease` object definition (`spec.chart`, `spec.values`, `spec.valuesFrom`, etc.).
    -   The specific source revision (commit SHA) if the chart source requires it (e.g., HelmRepository, Git, OCI).
    -   Resolved values (inline `spec.values` merged with content from `spec.valuesFrom` resources).
-   **Workflow:**
    1.  **Chart Retrieval:** Fetch or locate the Helm chart based on `spec.chart`. This might involve the `SourceController` (for Git/OCI sources) or a dedicated Helm chart fetcher (for `HelmRepository` sources).
    2.  **Values Resolution:** Gather and merge values from `spec.values` and all `spec.valuesFrom` references. Accessing content for `valuesFrom` needs a defined mechanism (e.g., querying the state store for rendered objects).
    3.  **Template Execution:** Run `helm template` (or equivalent) using the retrieved chart and resolved values.
    4.  **Status Update:** Record the build result (success/failure, errors) and a reference to the output manifests in the state store associated with the `HelmRelease` object.
-   **Outputs:**
    -   Rendered Kubernetes manifests.
    -   Updated status for the `HelmRelease` object in the state store.

### Object State Management

The core of the redesigned `flux-local` will be a central **Object State Store**, likely an in-memory database or structured data store. This store replaces the implicit state tracking of the current visitor pattern with an explicit, queryable repository. Its primary purpose is to track the definitions, processing status, and generated artifacts of all relevant Flux objects (`GitRepository`, `Kustomization`, `HelmRelease`, etc.) during a run.

**Stored Information Per Object (Conceptual):**

For each processed object (identified uniquely, e.g., by `kind/namespace/name`), the store will maintain entries containing:

-   **Definition:** The original parsed Custom Resource definition.
-   **Status:** The current processing state (e.g., `Pending`, `Fetching`, `Building`, `Ready`, `Failed`).
-   **Error Message:** Detailed error if `Status` is `Failed`.
-   **Source Artifact Info (for Source Objects like `GitRepository`):**
    -   `ResolvedRevision`: The specific commit SHA fetched.
    -   `CachedPath`: The local filesystem path to the checked-out code for the `ResolvedRevision`. *(Note: The store may need to map a sourceRef to multiple revision/path pairs if different builds require different versions).*
-   **Build Artifact Info (for Build Objects like `Kustomization`, `HelmRelease`):**
    -   `RenderedManifests`: The resulting Kubernetes YAML output (potentially as a string or parsed objects).

**Key Interactions & Role:**

-   **Initialization:** Populated with all discovered Flux CRs at the start.
-   **Dependency Resolution (`KustomizeController`):** Queries the `Status` of objects listed in `spec.dependsOn` to ensure they are `Ready` before proceeding.
-   **Source Location (`KustomizeController`, `HelmReleaseController`):** Queries the store (providing `sourceRef` and target revision SHA) to get the `CachedPath` for the required source code.
-   **`valuesFrom` Resolution (`HelmReleaseController`):** Queries the store for the `RenderedManifests` of previously built `ConfigMap` or `Secrets` referenced in `spec.valuesFrom`. This implies the manifests might need basic parsing or indexing within the store.
-   **Status & Artifact Updates (All Controllers):** After processing an object, controllers update its entry in the store with the final `Status`, any `ErrorMessage`, and `RenderedManifests` or source `CachedPath`/`ResolvedRevision`.
-   **Final Output:** The aggregated `RenderedManifests` from all successfully built `Kustomization` and `HelmRelease` objects form the final output of `flux-local`.
-   **Debugging & Multi-Error Reporting:** By storing individual statuses, the system can report *all* failures across different objects at the end of a run, rather than stopping at the first error.

This centralized state store provides the necessary decoupling between controllers and manages the flow of information (dependencies, source paths, intermediate artifacts) required for the build process.

---

## Project Sequencing: Initial Phases

To incrementally build and validate the new architecture, the following phased approach will be used:

**Phase 1: SourceController Implementation**
- Implement the SourceController as a self-contained component.
- Focus on:
    - Fetching and caching Git/OCI sources at specific revisions.
    - Handling authentication (basic support).
    - Defining and exposing the API for retrieving source artifact paths and revisions.
- Provide basic CLI/testing hooks to validate source fetch and cache logic in isolation.

**Phase 2: KustomizeController Integration**
- Implement the KustomizeController, consuming the SourceController API.
- Focus on:
    - Building manifests from Kustomization objects using sources and paths provided by SourceController.
    - Basic dependency handling (spec.dependsOn).
    - Storing build status and outputs in the Object State Store.
- Enable a simple sequential orchestration: fetch sources, then build kustomizations.

**Phase 3: HelmReleaseController Integration**
- Implement the HelmReleaseController, consuming the SourceController API and Object State Store.
- Focus on:
    - Chart retrieval (from Git/OCI/HelmRepository sources).
    - Values resolution (including valuesFrom, using the Object State Store).
    - Template rendering and status/artifact updates.

**Phase 4: Orchestration and Async Graph**
- Begin replacing sequential orchestration with an async task graph.
- Dynamically build the dependency graph from discovered objects and their relationships.
- Enable parallel execution of independent tasks and multi-error reporting.
