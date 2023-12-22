"""Library for formatting output."""

from typing import Generator, Any

import sys
from typing import TextIO
import yaml


PADDING = 4


def column_format_string(rows: list[list[str]]) -> str:
    """Produce a format string based on max width of columns."""
    num_cols = len(rows[0])
    widths = [0] * num_cols
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(str(value)))
    return "".join([f"{{:{w+PADDING}}}" for w in widths])


def format_columns(
    headers: list[str], rows: list[list[str]]
) -> Generator[str, None, None]:
    """Print the specified output rows in a column format."""
    data = [headers] + rows
    format_string = column_format_string(data)
    if format_string:
        for row in data:
            yield format_string.format(*[str(x) for x in row])


class PrintFormatter:
    """A formatter that prints human readable console output."""

    def __init__(self, keys: list[str] | None = None):
        """Initialize the PrintFormatter with optional keys to print."""
        self._keys = keys

    def format(self, data: list[dict[str, Any]]) -> Generator[str, None, None]:
        """Format the data objects."""
        if not data:
            return
        keys = self._keys if self._keys is not None else list(data[0])
        rows = []
        for row in data:
            rows.append([str(row[key]) for key in keys])
        cols = [col.upper() for col in keys]
        for result in format_columns(cols, rows):
            yield result

    def print(self, data: list[dict[str, Any]], file: TextIO = sys.stdout) -> None:
        """Output the data objects."""
        for result in self.format(data):
            print(result, file=file)


class YamlFormatter:
    """A formatter that prints yaml output."""

    def format(self, data: list[dict[str, Any]]) -> Generator[str, None, None]:
        """Format the data objects."""
        for line in yaml.dump_all(data, sort_keys=False, explicit_start=True).split(
            "\n"
        ):
            yield line

    def print(self, data: list[dict[str, Any]], file: TextIO = sys.stdout) -> None:
        """Format the data objects."""
        print(
            yaml.dump_all(data, sort_keys=False, explicit_start=True), end="", file=file
        )
