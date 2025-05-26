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
- Failures stop the entire process, so you can't see all failures at once

## Overview

The proposed direction is to redesign to mimic the internals of flux and
make the design work like controllers / operators.

### Controllers

In flux there are a few existing controllers:

- `SourceController` which is a controller that watches `GitRepository` and
  `OCIRepository` objects and updates the local git repo. This controller
  exposes a service that can be used to checkout a specific revision of the
  git repo used by other controllers.
- `KustomizationController` which is a controller that watches `Kustomization`
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

The `SourceController` (or the central state store it updates) needs to provide a mechanism for other controllers (`KustomizationController`, `HelmReleaseController`) to retrieve the local filesystem path for a specific revision of a requested source artifact.

When a `Kustomization` or `HelmRelease` specifies a `sourceRef` (e.g., `{ kind: GitRepository, name: my-repo }`), the respective controller will query for the cached artifact associated with `my-repo`. The query needs to resolve to a specific revision (commit SHA) based on the `GitRepository` object's status or potentially a user override. The query result should include:

- **Local Path:** The absolute path to the directory containing the checked-out source code for the **specific revision** in the local cache.
- **Revision:** The specific revision (commit SHA) that was fetched.
- **Status:** Indication of success or failure of the fetch operation.

This interface ensures that build controllers (`KustomizationController`, `HelmReleaseController`) can reliably access the correct source code needed for their operations without needing to manage the fetching logic themselves.

### KustomizationController

The `KustomizationController` is responsible for processing `Kustomization` custom resources and generating the corresponding Kubernetes manifests.

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
-   **Dependency Resolution (`KustomizationController`):** Queries the `Status` of objects listed in `spec.dependsOn` to ensure they are `Ready` before proceeding.
-   **Source Location (`KustomizationController`, `HelmReleaseController`):** Queries the store (providing `sourceRef` and target revision SHA) to get the `CachedPath` for the required source code.
-   **`valuesFrom` Resolution (`HelmReleaseController`):** Queries the store for the `RenderedManifests` of previously built `ConfigMap` or `Secrets` referenced in `spec.valuesFrom`. This implies the manifests might need basic parsing or indexing within the store.
-   **Status & Artifact Updates (All Controllers):** After processing an object, controllers update its entry in the store with the final `Status`, any `ErrorMessage`, and `RenderedManifests` or source `CachedPath`/`ResolvedRevision`.
-   **Final Output:** The aggregated `RenderedManifests` from all successfully built `Kustomization` and `HelmRelease` objects form the final output of `flux-local`.
-   **Debugging & Multi-Error Reporting:** By storing individual statuses, the system can report *all* failures across different objects at the end of a run, rather than stopping at the first error.

This centralized state store provides the necessary decoupling between controllers and manages the flow of information (dependencies, source paths, intermediate artifacts) required for the build process.

---

## Project Status

### Completed Phases

**Phase 1: SourceController Implementation** ✅
- Implemented as a self-contained component
- Handles Git and OCI repository fetching and caching
- Includes support for different reference types (branch, tag, commit)
- Provides a clean API for other controllers to access sources

**Phase 2: KustomizationController Implementation** ✅
- Implements Kustomization resource handling
- Integrates with SourceController for source resolution
- Handles dependency management (spec.dependsOn)
- Stores build status and outputs in the Object State Store

### In Progress

**Phase 3: HelmReleaseController Integration**  🔄
- [x] Implement HelmRelease resource handling
  - Basic controller implementation complete
  - Handles HelmRelease lifecycle and state management
  - Integrates with the central Store

- [x] Integrate with SourceController for chart sources
  - Supports Git and OCI repositories
  - Handles repository authentication
  - Manages chart dependencies

- [~] Handle values resolution (including valuesFrom)
  - Basic implementation exists in `values.py`
  - Supports ConfigMap and Secret references
  - ❗ Not yet fully integrated with reconciliation flow

- [x] Implement template rendering
  - Uses existing Helm class for rendering
  - Handles chart templating
  - Supports various chart sources (Git, OCI, local)

**Current Limitations**
- Values resolution needs integration with reconciliation
- Limited test coverage for Helm-specific features
- Basic error handling could be enhanced
- Documentation needs improvement

**Phase 4: Shell and CLI Improvements**
- [x] Basic interactive shell implementation
- [ ] Enhance shell with more commands and better UX
- [ ] Add more CLI commands for common operations

### Future Work

**Phase 5: Advanced Orchestration**
- [ ] Implement async task graph for parallel execution
- [ ] Add support for dynamic dependency resolution
- [ ] Improve error handling and reporting
- [ ] Add support for Flux Operator resources

**Phase 6: Performance Optimizations**
- [ ] Implement incremental builds
- [ ] Add caching for build artifacts
- [ ] Optimize memory usage for large clusters

## Codebase Navigation

This section provides a quick reference for understanding and navigating the codebase, particularly focusing on the new controller-based architecture.

### Core Components

1. **Store (`store/`)**
   - `store.py`: Defines the abstract `Store` interface for managing state
   - `in_memory.py`: Implements `InMemoryStore` - the in-memory implementation of the Store
   - Key concepts:
     - Tracks objects by `NamedResource` (kind/namespace/name)
     - Manages three types of data: objects, status, and artifacts
     - Supports event listeners for object changes

2. **Source Controller (`source_controller/`)**
   - `controller.py`: Main `SourceController` class that manages source artifacts
   - `git.py`: Handles Git repository operations
   - `oci.py`: Handles OCI repository operations
   - `cache.py`: Manages repository caching
   - Key features:
     - Fetches and caches Git/OCI repositories
     - Handles different reference types (branch, tag, commit, semver)
     - Provides a clean interface for other controllers to access sources
     - Supports authentication for private repositories

3. **Kustomize Controller (`kustomize_controller/`)**
   - `controller.py`: Main `KustomizationController` class
   - `artifact.py`: Defines the `KustomizationArtifact` class
   - Key features:
     - Processes Kustomization resources
     - Builds kustomizations using the flux CLI
     - Manages dependencies between resources
     - Handles resource pruning and health assessment

4. **Helm Controller (`helm_controller/`)**
   - `controller.py`: Main `HelmReleaseController` class
   - `artifact.py`: Defines the `HelmReleaseArtifact` class
   - Key features:
     - Manages HelmRelease resources
     - Integrates with Helm for chart operations
     - Supports values resolution from ConfigMaps and Secrets
     - Handles chart dependencies and repository management

5. **Helm Integration (`helm/`)**
   - `helm.py`: Core Helm operations and repository management
   - `oci.py`: OCI registry operations for Helm charts
   - Key features:
     - Chart installation and templating
     - Repository management (add/update/remove)
     - Support for various chart sources (Git, OCI, local)
     - Values file processing and merging

6. **Manifest Models (`manifest.py`)**
   - Defines all core data models using dataclasses
   - Uses mashumaro for serialization/deserialization
   - Key models:
     - `GitRepository`: Source for Git-based repositories
     - `OCIRepository`: Source for OCI-based repositories
     - `Kustomization`: Kustomize configuration and resources
     - `HelmRelease`: Helm release specification
     - `HelmRepository`: Helm chart repository configuration
     - `ConfigMap`/`Secret`: Kubernetes resources for configuration

7. **Values Processing (`values.py`)**
   - Handles values resolution for Helm releases
   - Supports `valuesFrom` references to ConfigMaps and Secrets
   - Implements deep merging of values
   - Handles value placeholders and substitutions

8. **Tooling (`tool/`)**
   - `shell/`: Interactive shell for exploring resources
   - `get.py`: CLI commands for retrieving resources
   - `build.py`: Commands for building and validating resources
   - `check.py`: Validation and linting tools
   - `diff.py`: Resource comparison utilities

9. **Testing (`tests/`)**
   - Unit and integration tests
   - Test fixtures and helpers
   - End-to-end test scenarios
   - Test data and mocks

### Key Patterns

1. **Event-Driven Architecture**
   - Uses an event system to notify components of changes
   - Controllers register listeners for specific events
   - Enables loose coupling between components

2. **Async-First**
   - Most operations are asynchronous
   - Uses Python's asyncio for concurrency
   - Supports parallel processing of independent operations

3. **Immutability**
   - Data models are immutable (frozen dataclasses)
   - Changes create new objects rather than modifying existing ones
   - Helps with reasoning about state changes

### Common Code Paths

1. **Adding a New Object**
   - Object created and added to store via `store.add_object()`
   - Triggers `OBJECT_ADDED` event
   - Relevant controllers pick up the event and process the object

2. **Processing a GitRepository**
   - `SourceController` handles `GitRepository` objects
   - Fetches repository using `git.fetch_git()`
   - Caches the repository using `GitCache`
   - Updates status in store when complete

3. **Processing a Kustomization**
   - `KustomizationController` handles `Kustomization` objects
   - Resolves source references to get local paths
   - Runs `kustomize build` on the target path
   - Stores results in the store

### Development Tips

1. **Testing**
   - Look in `tests/` for corresponding test files
   - Many tests use fixtures to set up test data
   - Async tests use `pytest-asyncio`

2. **Debugging**
   - Set `LOG_LEVEL=DEBUG` for detailed logging
   - The store's event system can be used to trace object flow
   - The shell (`flux-local shell`) is useful for interactive exploration

3. **Extending**
   - New controllers should implement the controller pattern
   - Register for relevant events in the store
   - Follow the async pattern for I/O operations


## Pending Bugs & improvements

- [ ] Does not handle target namespace
- [ ] Does not re-reconcile on state change
- [ ] Helm controller does not retry when HelmRepository appears
- [ ] NamedResource should be buildable from BaseManifest
- [ ] State events should be intent based (e.g. "is ready")
- [x] in memory store tests hang. In the middle of adding watches to simplify controllers
