# Bootstrapping Process

This document describes the bootstrapping process for flux-local.

## Overview

The bootstrapping process initializes the system by loading initial resources and starting the reconciliation process. This is typically the first step when starting flux-local.

## Bootstrapping Process

The bootstrapping process consists of the following steps:

1. **Resource Loading**: Load initial resources from the filesystem
   - Supports both files and directories
   - Recursively processes subdirectories by default
   - Handles YAML/JSON parsing and validation

2. **Controller Initialization**: Initialize and start all controllers
   - SourceController: Manages Git and OCI repositories
   - KustomizationController: Handles Kustomization resources
   - HelmReleaseController: Manages Helm releases (if enabled)

3. **Reconciliation**: Begin the reconciliation process
   - Processes all loaded resources
   - Handles dependencies between resources
   - Updates resource statuses

## Error Handling

The bootstrap process will return `False` if any errors occur during loading or reconciliation. Detailed error information will be logged.

## Resource Loading Options

The `LoadOptions` class provides several options to customize the resource loading behavior:

```python
options = LoadOptions(
    path=Path("/path/to/resources"),  # Path to load resources from
    recursive=True,                   # Whether to load from subdirectories
)
```

## Best Practices

1. **Error Handling**: Always check the return value of the bootstrap function
2. **Resource Cleanup**: Use `try/finally` to ensure proper cleanup
3. **Logging**: Configure logging to capture detailed information about the bootstrap process
4. **Path Handling**: Use `pathlib.Path` for cross-platform path handling

## Troubleshooting

- **Missing Resources**: Check that the path exists and contains valid YAML/JSON files
- **Permission Issues**: Ensure the application has read access to the specified path
- **Validation Errors**: Check the logs for details about any validation failures

## Phase 1: Bootstrap

### Purpose
- Load initial Kubernetes manifests from the filesystem
- Populate the store with the initial state
- Set up the foundation for controllers to operate

### Components

#### ResourceLoader (`orchestrator/loader.py`)
- **Responsible for**:
  - Loading YAML/JSON manifests from files and directories
  - Basic validation of Kubernetes resources
  - Simple filtering (CRDs, Secrets, etc.)
  - Populating the store with initial resources

- **Key Characteristics**:
  - Used only during system initialization
  - Stateless - all state is stored in the provided Store
  - Not thread-safe - designed for single-threaded bootstrap
  - Minimal dependencies - only requires the Store interface

- **Not Responsible For**:
  - Building kustomizations
  - Rendering Helm charts
  - Managing repository sources
  - Handling reconciliation

### Process Flow
1. System starts up
2. ResourceLoader is instantiated with a Store
3. Loader reads manifests from the specified paths
4. Loader parses and validates the manifests
5. Loader adds the resources to the store
6. Bootstrapping completes

## Phase 2: Runtime

### Purpose
- Process resources using the appropriate controllers
- Handle complex operations like building and rendering
- Manage the reconciliation loop
- Update resource statuses

### Components

#### Controllers
- **SourceController**: Manages Git/OCI repositories
- **KustomizationController**: Handles Kustomization resources
- **HelmReleaseController**: Manages Helm releases

#### Store
- Maintains the current state of all resources
- Provides a consistent interface for accessing resources
- Handles resource versioning and updates

### Process Flow
1. After bootstrap, the orchestrator starts the controllers
2. Each controller watches for resources it's responsible for
3. When a resource is created/updated:
   - Controller loads the resource from the store
   - Performs any necessary operations (building, rendering, etc.)
   - Updates the store with the results
   - Updates the resource status

## Error Handling

### During Bootstrap
- File not found errors are fatal
- Invalid YAML/JSON is logged and skipped
- Resource validation failures are logged

### During Runtime
- Controllers handle their own errors
- Failed operations are retried according to the controller's policy
- Status updates reflect the current state of resources

## Performance Considerations

### Bootstrap Phase
- Loads all resources into memory
- Should be kept minimal
- Only includes the initial set of resources

### Runtime Phase
- Controllers only process resources they're responsible for
- State is maintained in the store
- Operations are asynchronous and non-blocking
