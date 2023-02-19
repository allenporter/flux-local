"""Tests for the format library."""

from flux_local.tool.format import format_columns, PrintFormatter


def test_format_columns_empty() -> None:
    """Tests with no rows."""
    assert list(format_columns([], [])) == []


def test_format_columns_empty_rows() -> None:
    """Tests with no rows."""
    assert list(format_columns(["a", "b", "c"], [])) == ["a    b    c    "]


def test_format_columns_rws() -> None:
    """Tests format with normal rows"""
    assert list(
        format_columns(
            ["name", "namespace"], [["podinfo", "podinfo"], ["metallb", "network"]]
        )
    ) == [
        "name       namespace    ",
        "podinfo    podinfo      ",
        "metallb    network      ",
    ]


def test_print_formatter() -> None:
    """Print formatting with empty data."""
    formatter = PrintFormatter()
    assert list(formatter.format([])) == []


def test_print_formatter_data() -> None:
    """Print formatting with empty data."""
    formatter = PrintFormatter()
    assert list(
        formatter.format(
            [
                {
                    "name": "podinfo",
                    "namespace": "podinfo",
                },
                {
                    "name": "metallb",
                    "namespace": "network",
                },
            ]
        )
    ) == [
        "name       namespace    ",
        "podinfo    podinfo      ",
        "metallb    network      ",
    ]
