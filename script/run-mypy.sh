#!/usr/bin/env bash

set -o errexit

# Change directory to the project root directory.
cd "$(dirname "$0")"

pip3 install -r requirements_dev.txt --no-input --quiet

mypy .
