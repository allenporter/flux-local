## Python virtual environment

Python virtual environments are used to isolate python projects from each other.
Virtual environments are typically stored in a directory called 'venv'. The
virtual environment is created using the 'venv' command line tool.

Example:
```bash
$ uv venv
$ source .venv/bin/activate
$ uv pip install -r requirements_dev.txt
```

## Testing

Testing is important for any project, and this project is no different. The project
uses the pytest testing framework, and the tests are located in the tests/ directory.
The tests are run automatically on every commit and pull request.

To run the tests locally, you can use the following command:

```bash
$ pytest
```

Some tests used `syrup` to test against golden snapshot files. These can be updated with:
```bash
$ pytest --snapshot-update
```

## Documentation

Documentation is important for any project. This project uses the pdoc documentation
tool to generate documentation from the source code. The documentation is located in
the docs/ directory.

To generate the documentation locally, you can use the following command:

```bash
$ pdoc flux_local -o docs/
```
