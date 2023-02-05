"""
flux-local is a command line program that calls the flux-local library.

The main things that happen in the program are:
  - Reads the training data markdown file
  - Creates prompts to include the necessary training data
  - Parses the response output filenames and files
  - Writes the files to disk or prints them on the screen

The prompts must be created so they result in structured output that can be parsed by the
program. The program handles creating the prompts as well as parsing the response to pull
out the structured response with filenames and file contents.

Since the input/output files may be large, the program breaks down the requests into
smaller pieces to avoid any API limits.

Example usage:
  ./flux-local.py --api-key=<KEY> --project-file ./project.yaml --training-data ./project.md --output-dir=/tmp/project
"""

import argparse

import api


def main():
    """flux-local command line tool."""
    parser = argparse.ArgumentParser(description="flux-local")
    args = parser.parse_args()

    print(api.call(args[0]))


if __name__ == "__main__":
    main()
