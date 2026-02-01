---
name: project_development
description: Use standardized scripts to manage the project environment, testing, and linting.
---

# Project Development Skill

This skill teaches you how to interact with the Supernote-Lite codebase using standardized scripts, following the "Scripts to Rule Them All" pattern.

## Core Scripts

| Script | When to use |
| :--- | :--- |
| `script/bootstrap` | After cloning or when dependencies change. |
| `script/setup` | When activating a virtual environment before development. |
| `script/test` | Before submitting changes or to verify functionality. |
| `script/lint` | Before committing to ensure code style and quality. |
| `script/update` | When the repo is stale and there are changes to sync down from git. |

## Usage Patterns

### Standard Development Flow
1. **Initialize**: `./script/bootstrap` first time the repo is created
2. **Setup**: `./script/setup` to activat the virtual environment
3. **Implement**: Make your changes to the code.
4. **Lint**: `./script/lint` to check for style issues.
5. **Test**: `./script/test` to run the test suite.

### Notes
- All scripts are located in the `script/` directory at the project root.
- Scripts are designed to be run from the project root.
- The scripts will automatically use `uv` if it is installed, otherwise they will fall back to standard Python tools.
