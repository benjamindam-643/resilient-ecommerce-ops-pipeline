"""Fail-safe helpers for replacing data ranges in Google Sheets.

Production exporters used to clear their live range before writing. A quota,
timeout, or 10-million-cell error after that clear left the worksheet blank.
These helpers validate capacity first, write complete row batches within the
recommended request size, and only then remove stale cells.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

SHEETS_MAX_CELLS = 10_000_000
SHEETS_RECOMMENDED_PAYLOAD_BYTES = 1_800_000
SHEETS_MAX_ROWS_PER_BATCH = 10_000

_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$", re.IGNORECASE)
_RANGE_RE = re.compile(r"^([A-Z]+)(\d+):([A-Z]+)(\d*)$", re.IGNORECASE)


class SheetCapacityError(RuntimeError):
    """Raised before any write when the workbook would exceed its cell limit."""


def column_number(label: str) -> int:
    value = 0
    for char in label.upper():
        if not "A" <= char <= "Z":
            raise ValueError(f"Cot Google Sheet khong hop le: {label!r}")
        value = value * 26 + ord(char) - ord("A") + 1
    if value < 1:
        raise ValueError(f"Cot Google Sheet khong hop le: {label!r}")
    return value


def column_label(number: int) -> str:
    if number < 1:
        raise ValueError(f"So cot Google Sheet khong hop le: {number}")
    chars: list[str] = []
    while number:
        number, remainder = divmod(number - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def _cell_parts(a1_range: str) -> tuple[int, int]:
    cell = str(a1_range).rsplit("!", 1)[-1].split(":", 1)[0].replace("$", "")
    match = _CELL_RE.fullmatch(cell)
    if not match:
        raise ValueError(f"Dia chi Google Sheet khong hop le: {a1_range!r}")
    return column_number(match.group(1)), int(match.group(2))


def _range_parts(clear_range: str) -> tuple[int, int, int]:
    raw = str(clear_range).rsplit("!", 1)[-1].replace("$", "")
    match = _RANGE_RE.fullmatch(raw)
    if not match:
        raise ValueError(f"Vung Google Sheet khong hop le: {clear_range!r}")
    start_col = column_number(match.group(1))
    start_row = int(match.group(2))
    end_col = column_number(match.group(3))
    if start_col > end_col or start_row < 1:
        raise ValueError(f"Vung Google Sheet khong hop le: {clear_range!r}")
    return start_col, start_row, end_col


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _update_extent(update: dict[str, Any]) -> tuple[int, int, int, int]:
    start_col, start_row = _cell_parts(str(update.get("range") or ""))
    values = update.get("values")
    if not isinstance(values, list) or not values:
        return start_col, start_row, start_col, start_row
    width = max(
        (len(row) if isinstance(row, list) else 1 for row in values),
        default=1,
    )
    return start_col, start_row, start_col + max(width, 1) - 1, start_row + len(values) - 1


def _workbook_worksheets(spreadsheet: Any, worksheet: Any) -> list[Any]:
    try:
        worksheets = spreadsheet.worksheets()
    except Exception:  # noqa: BLE001 - compatibility with thin test doubles
        worksheets = None
    if not isinstance(worksheets, (list, tuple)):
        return [worksheet]
    result = list(worksheets)
    target_id = getattr(worksheet, "id", None)
    target_title = getattr(worksheet, "title", None)
    for item in result:
        if item is worksheet:
            return result
        if target_id is not None and getattr(item, "id", None) == target_id:
            return result
        if isinstance(target_title, str) and getattr(item, "title", None) == target_title:
            return result
    result.append(worksheet)
    return result


def validate_workbook_capacity(
    spreadsheet: Any,
    worksheet: Any,
    updates: Iterable[dict[str, Any]],
) -> dict[str, int]:
    """Validate the projected allocated grid before writing any values."""
    update_list = list(updates)
    current_rows = _positive_int(getattr(worksheet, "row_count", None), 1)
    current_cols = _positive_int(getattr(worksheet, "col_count", None), 1)
    required_rows = current_rows
    required_cols = current_cols
    for update in update_list:
        _start_col, _start_row, end_col, end_row = _update_extent(update)
        required_rows = max(required_rows, end_row)
        required_cols = max(required_cols, end_col)

    worksheets = _workbook_worksheets(spreadsheet, worksheet)
    current_total = 0
    target_seen = False
    target_id = getattr(worksheet, "id", None)
    target_title = getattr(worksheet, "title", None)
    for item in worksheets:
        rows = _positive_int(getattr(item, "row_count", None), 1)
        cols = _positive_int(getattr(item, "col_count", None), 1)
        current_total += rows * cols
        same_object = item is worksheet
        same_id = target_id is not None and getattr(item, "id", None) == target_id
        same_title = target_title and getattr(item, "title", None) == target_title
        target_seen = target_seen or bool(same_object or same_id or same_title)

    current_grid = current_rows * current_cols
    if not target_seen:
        current_total += current_grid
    projected_grid = required_rows * required_cols
    projected_total = current_total - current_grid + projected_grid
    if projected_total > SHEETS_MAX_CELLS:
        title = str(target_title or "worksheet")
        raise SheetCapacityError(
            f"Google Sheet '{title}' se vuot gioi han {SHEETS_MAX_CELLS:,} o "
            f"(hien tai {current_total:,}, du kien {projected_total:,}). "
            "Khong ghi/xoa du lieu. Hay cau hinh them workbook part "
            "(sheet_month_*_order_p1, p2, p3)."
        )
    return {
        "current_cells": current_total,
        "projected_cells": projected_total,
        "required_rows": required_rows,
        "required_columns": required_cols,
    }


def _compact_column_ranges(
    columns: list[int],
    start_row: int,
    end_row: int,
) -> list[str]:
    if not columns or end_row < start_row:
        return []
    ranges: list[str] = []
    group_start = previous = columns[0]
    for number in columns[1:] + [columns[-1] + 2]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(
            f"{column_label(group_start)}{start_row}:"
            f"{column_label(previous)}{end_row}"
        )
        group_start = previous = number
    return ranges


def _offset_update(update: dict[str, Any], offset: int, end: int) -> dict[str, Any] | None:
    values = update["values"]
    if offset >= len(values):
        return None
    prefix, _, cell = str(update["range"]).rpartition("!")
    start_col, start_row = _cell_parts(cell)
    range_prefix = f"{prefix}!" if prefix else ""
    return {
        "range": f"{range_prefix}{column_label(start_col)}{start_row + offset}",
        "values": values[offset:min(end, len(values))],
    }


def _payload_size(updates: list[dict[str, Any]]) -> int:
    payload = {"valueInputOption": "RAW", "data": updates}
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )


def _write_batches(update_list: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split large writes into row-complete requests near Sheets' 2 MB guidance."""
    max_rows = max((len(update["values"]) for update in update_list), default=0)
    batches: list[list[dict[str, Any]]] = []
    offset = 0
    while offset < max_rows:
        row_count = min(SHEETS_MAX_ROWS_PER_BATCH, max_rows - offset)
        while True:
            end = offset + row_count
            batch = [
                sliced
                for update in update_list
                if (sliced := _offset_update(update, offset, end)) is not None
            ]
            if row_count <= 1 or _payload_size(batch) <= SHEETS_RECOMMENDED_PAYLOAD_BYTES:
                break
            row_count = max(1, row_count // 2)
        batches.append(batch)
        offset += row_count
    return batches


def safe_replace_values(
    spreadsheet: Any,
    worksheet: Any,
    updates: Iterable[dict[str, Any]],
    *,
    data_rows: int,
    clear_range: str,
    clear_on_empty: bool = False,
) -> bool:
    """Replace live values; never clear good data before the new write succeeds."""
    if data_rows <= 0:
        if clear_on_empty:
            worksheet.batch_clear([clear_range])
            return True
        return False

    update_list = [
        {"range": update["range"], "values": update["values"]}
        for update in updates
    ]
    if not update_list:
        raise RuntimeError("Khong co cot Google Sheet nao de ghi; giu nguyen du lieu cu.")

    capacity = validate_workbook_capacity(spreadsheet, worksheet, update_list)
    current_rows = _positive_int(getattr(worksheet, "row_count", None), 1)
    current_cols = _positive_int(getattr(worksheet, "col_count", None), 1)
    required_rows = capacity["required_rows"]
    required_cols = capacity["required_columns"]
    if required_rows > current_rows or required_cols > current_cols:
        worksheet.resize(
            rows=max(current_rows, required_rows),
            cols=max(current_cols, required_cols),
        )
    for batch in _write_batches(update_list):
        spreadsheet.values_batch_update({
            "valueInputOption": "RAW",
            "data": batch,
        })

    clear_start_col, data_start_row, clear_end_col = _range_parts(clear_range)
    data_end_row = data_start_row + data_rows - 1
    updated_columns: set[int] = set()
    for update in update_list:
        start_col, start_row, end_col, _end_row = _update_extent(update)
        if start_row != data_start_row:
            continue
        lower = max(start_col, clear_start_col)
        upper = min(end_col, clear_end_col)
        if lower <= upper:
            updated_columns.update(range(lower, upper + 1))

    missing_columns = [
        number
        for number in range(clear_start_col, clear_end_col + 1)
        if number not in updated_columns
    ]
    clear_ranges = _compact_column_ranges(
        missing_columns,
        data_start_row,
        data_end_row,
    )
    old_row_count = _positive_int(getattr(worksheet, "row_count", None), data_end_row)
    stale_start = data_end_row + 1
    if stale_start <= old_row_count:
        clear_ranges.append(
            f"{column_label(clear_start_col)}{stale_start}:"
            f"{column_label(clear_end_col)}{old_row_count}"
        )
    if clear_ranges:
        worksheet.batch_clear(clear_ranges)
    return True
