"""Tables as CSV text that opens correctly in a Turkish spreadsheet.

Pure functions, standard library only. The text starts with a UTF-8 byte
order mark so Excel detects the encoding, uses ``;`` as the delimiter (the
list separator of a Turkish Excel) and ``\r\n`` line endings.

Course and assignment titles come from university pages, so a text cell that
a spreadsheet would evaluate as a formula is prefixed with a single quote.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

CSV_MIME_TYPE = "text/csv"
BOM = "﻿"
DELIMITERS = (";", ",")
# About 200 KB of inline text: more than any real term or transcript needs.
MAX_INLINE_BYTES = 200_000
# A spreadsheet evaluates a cell that starts with one of these.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
YES, NO = "Evet", "Hayır"


def _number_text(value: int | float | Decimal, decimal_mark: str) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        if value.is_integer():
            return str(int(value))
        value = Decimal(repr(value))
    elif isinstance(value, Decimal) and not value.is_finite():
        return ""
    text = format(value, "f")
    return text.replace(".", decimal_mark) if decimal_mark != "." else text


def cell_text(value: Any, delimiter: str = ";") -> str:
    """One cell as text.

    A number is written as a number, with a decimal comma when the delimiter
    is ``;``. Only a numeric value can produce a cell that starts with a
    minus sign: text that starts with ``=``, ``+``, ``-``, ``@``, a tab or a
    carriage return gets a leading single quote, so it stays text.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return YES if value else NO
    if isinstance(value, (int, float, Decimal)):
        return _number_text(value, "," if delimiter == ";" else ".")
    text = str(value)
    if text.startswith(FORMULA_TRIGGERS):
        return "'" + text
    return text


def render_csv(
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    delimiter: str = ";",
    max_bytes: int | None = None,
) -> tuple[str, int]:
    """``(text, rows_written)``: the header and the rows as CSV text with a BOM.

    With ``max_bytes`` the text stops before the row that would take its
    UTF-8 size over the limit; the header is always written.
    """
    if delimiter not in DELIMITERS:
        raise ValueError('delimiter must be ";" or ",".')
    width = len(columns)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    def line(cells: Sequence[Any]) -> str:
        buffer.seek(0)
        buffer.truncate()
        writer.writerow([cell_text(cell, delimiter) for cell in cells])
        return buffer.getvalue()

    parts = [BOM, line(columns)]
    size = sum(len(part.encode("utf-8")) for part in parts)
    written = 0
    for row in rows:
        if len(row) != width:
            raise ValueError(f"A row has {len(row)} cells; the table has {width} columns.")
        text = line(row)
        size += len(text.encode("utf-8"))
        if max_bytes is not None and size > max_bytes:
            break
        parts.append(text)
        written += 1
    return "".join(parts), written
