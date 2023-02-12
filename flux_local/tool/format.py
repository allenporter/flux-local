"""Library for formatting output."""


PADDING = 4


def column_format_string(rows: list[list[str]]) -> str:
    """Produce a format string based on max width of columns."""
    num_cols = len(rows[0])
    widths = [0] * num_cols
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(str(value)))
    return "".join([f"{{:{w+PADDING}}}" for w in widths])


def print_columns(headers: list[str], rows: list[list[str]]) -> None:
    """Print the specified output rows in a column format."""
    data = [headers] + rows
    format_string = column_format_string(data)
    for row in data:
        print(format_string.format(*[str(x) for x in row]))
