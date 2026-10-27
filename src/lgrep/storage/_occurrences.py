"""Lossless single-file occurrence encoding; callers retain ordinary dictionaries."""

from __future__ import annotations

from lgrep.parser.symbols import make_occurrence_id

STORAGE_VERSION = 1

# Key order is part of canonical eligibility, so noncanonical rows stay untouched.
_FIELDS = (
    "id",
    "name",
    "file_path",
    "start_byte",
    "end_byte",
    "line_number",
    "line_text",
    "kind",
    "enclosing_symbol_id",
    "is_test_file",
)


def encode_occurrences(occurrences: dict[str, list[dict]]) -> tuple[dict, list[str]]:
    """Intern repeated snapshot strings and omit only exactly derivable fields."""
    strings: list[str] = []
    positions: dict[str, int] = {}

    def intern(value: str) -> int:
        position = positions.get(value)
        if position is None:
            position = len(strings)
            positions[value] = position
            strings.append(value)
        return position

    packed = {}
    for name, rows in occurrences.items():
        bucket = []
        for row in rows:
            if tuple(row) != _FIELDS:
                bucket.append(row)
                continue
            path = row["file_path"]
            start, end, line = row["start_byte"], row["end_byte"], row["line_number"]
            text, kind, enclosing = row["line_text"], row["kind"], row["enclosing_symbol_id"]
            flag = row["is_test_file"]
            if not (
                isinstance(path, str)
                and isinstance(text, str)
                and isinstance(kind, str)
                and (enclosing is None or isinstance(enclosing, str))
                and type(start) is int
                and type(end) is int
                and type(line) is int
                and type(flag) is bool
                and row["name"] == name
                and row["id"] == make_occurrence_id(path, name, start)
            ):
                bucket.append(row)
                continue
            bucket.append(
                [
                    intern(path),
                    start,
                    end,
                    line,
                    intern(text),
                    intern(kind),
                    None if enclosing is None else intern(enclosing),
                    flag,
                ]
            )
        packed[name] = bucket
    return packed, strings


def decode_occurrences(data: dict) -> dict[str, list[dict]]:
    """Reject ambiguous or malformed encoding before exposing any packed rows."""
    rows_by_name = data.get("occurrences", {})
    if not isinstance(rows_by_name, dict):
        raise ValueError("Invalid occurrence buckets")
    encoded = "storage_version" in data
    if encoded:
        version = data["storage_version"]
        if type(version) is not int or version != STORAGE_VERSION:
            raise ValueError("Unsupported occurrence storage version")
        strings = data.get("occurrence_strings")
        if not isinstance(strings, list) or any(not isinstance(value, str) for value in strings):
            raise ValueError("Invalid occurrence string table")

    def text(index) -> str:
        if type(index) is not int or not 0 <= index < len(strings):
            raise ValueError("Invalid occurrence string index")
        return strings[index]

    for name, rows in rows_by_name.items():
        if not isinstance(rows, list):
            raise ValueError("Invalid occurrence bucket")
        for position, row in enumerate(rows):
            if isinstance(row, dict):
                continue
            if not encoded or not isinstance(row, list) or len(row) != 8:
                raise ValueError("Invalid packed occurrence row")
            path, start, end, line, source, kind, enclosing, flag = row
            if type(start) is not int or type(end) is not int or type(line) is not int:
                raise ValueError("Invalid packed occurrence position")
            if type(flag) is not bool:
                raise ValueError("Invalid packed occurrence test flag")
            path = text(path)
            # This is the load's private parsed body. Replacing each packed row
            # releases it before the rest of the decoded index is allocated.
            rows[position] = {
                "id": make_occurrence_id(path, name, start),
                "name": name,
                "file_path": path,
                "start_byte": start,
                "end_byte": end,
                "line_number": line,
                "line_text": text(source),
                "kind": text(kind),
                "enclosing_symbol_id": None if enclosing is None else text(enclosing),
                "is_test_file": flag,
            }
    return rows_by_name
