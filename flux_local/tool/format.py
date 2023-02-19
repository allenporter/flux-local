"""Library for formatting output."""

from typing import Generator, Any
from abc import ABC, abstractmethod


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


def print_columns(headers: list[str], rows: list[list[str]]) -> None:
    """Print the specified output rows in a column format."""
    for line in format_columns(headers, rows):
        print(line)


class Formatter(ABC):
    """Outputs selected objects."""

    @abstractmethod
    def print(self, data: list[dict[str, Any]], keys: list[str] | None = None) -> None:
        """Output the data objects."""


class PrintFormatter:
    """A formatter that prints human readable console output."""

    def format(
        self, data: list[dict[str, Any]], keys: list[str] | None = None
    ) -> Generator[str, None, None]:
        """Format the data objects."""
        if not data:
            return
        if keys is None:
            keys = list(data[0])
        rows = []
        for row in data:
            rows.append([row[key] for key in keys])
        for result in format_columns(keys, rows):
            yield result

    def print(self, data: list[dict[str, Any]], keys: list[str] | None = None) -> None:
        """Output the data objects."""
        for result in self.format(data, keys):
            print(result)
