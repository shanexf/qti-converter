"""
CSV generation for admin data exports.

Includes protection against "CSV injection" (a.k.a. formula injection): if a
user-supplied field starts with =, +, -, or @, spreadsheet apps like Excel
can interpret it as a formula when the file is opened. Since fields like
full_name and institution are user-supplied, we neutralize this before
writing any cell.
"""
import csv
import io

_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


def _sanitize_cell(value):
    if value is None:
        return ""
    s = str(value)
    if s.startswith(_DANGEROUS_PREFIXES):
        return "'" + s  # leading apostrophe forces spreadsheet apps to treat it as text
    return s


def rows_to_csv(rows: list, columns: list) -> str:
    """rows: list of dicts. columns: ordered list of dict keys to include."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_sanitize_cell(row.get(col, "")) for col in columns])
    return buf.getvalue()
