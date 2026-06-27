from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import pandas as pd

from .cellstate import STATE_VALUE, cell_state
from .models import TameDataset


def category_value_profile(dataset: TameDataset) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in dataset.columns_with_tag("CATEGORY"):
        value_counts: dict[str, int] = {}
        for value in dataset.df[column.name]:
            if cell_state(value) != STATE_VALUE:
                continue
            raw_value = str(value).strip()
            value_counts[raw_value] = value_counts.get(raw_value, 0) + 1

        for raw_value, raw_count in value_counts.items():
            rows.append(
                {
                    "column": column.name,
                    "tags": "::".join(column.tags),
                    "raw_value": raw_value,
                    "raw_count": int(raw_count),
                }
            )

    columns = ["column", "tags", "raw_value", "raw_count"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns].sort_values(
        ["column", "raw_count", "raw_value"],
        ascending=[True, False, True],
        kind="stable",
    ).reset_index(drop=True)


def datetime_value_profile(dataset: TameDataset) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    columns = [*dataset.columns_with_tag("DATETIME"), *dataset.columns_with_tag("DATE")]
    for column in columns:
        if column.name in seen:
            continue
        seen.add(column.name)
        value_counts: dict[str, int] = {}
        for value in dataset.df[column.name]:
            if cell_state(value) != STATE_VALUE:
                continue
            raw_value = str(value).strip()
            value_counts[raw_value] = value_counts.get(raw_value, 0) + 1

        for raw_value, raw_count in value_counts.items():
            parsed = parse_temporal_value(raw_value, column.tags)
            rows.append(
                {
                    "column": column.name,
                    "tags": "::".join(column.tags),
                    "raw_value": raw_value,
                    "raw_count": int(raw_count),
                    "recognized": parsed is not None,
                    "parsed_value": None if parsed is None else parsed.isoformat(),
                }
            )

    columns = ["column", "tags", "raw_value", "raw_count", "recognized", "parsed_value"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns].sort_values(
        ["column", "recognized", "raw_count", "raw_value"],
        ascending=[True, False, False, True],
        kind="stable",
    ).reset_index(drop=True)


_TIME_ANCHOR_DATE = date(1900, 1, 1)


def parse_temporal_value(value: Any, tags: tuple[str, ...] | list[str] = ()) -> pd.Timestamp | None:
    fmt = _temporal_format(tags)
    if _has_tag(tags, "TIME") and not _has_tag(tags, "DATETIME") and not _has_tag(tags, "DATE"):
        return parse_time_value(value, fmt=fmt)
    if fmt is None:
        parsed = parse_datetime_value(value)
    else:
        if cell_state(value) != STATE_VALUE:
            return None
        text = str(value).strip()
        if not text:
            return None
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        if pd.isna(parsed):
            return None
        parsed = pd.Timestamp(parsed)
    if parsed is None:
        return None
    if _has_tag(tags, "DATE") and not _has_tag(tags, "DATETIME"):
        return parsed.normalize()
    return parsed


def parse_time_value(value: Any, *, fmt: str | None = None) -> pd.Timestamp | None:
    if cell_state(value) != STATE_VALUE:
        return None
    if isinstance(value, pd.Timestamp):
        return _timestamp_from_time(value.time())
    if isinstance(value, datetime):
        return _timestamp_from_time(value.time())
    if isinstance(value, time):
        return _timestamp_from_time(value)
    if isinstance(value, date):
        return None

    text = str(value).strip()
    if not text:
        return None
    if fmt is not None:
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        if pd.isna(parsed):
            return None
        return _timestamp_from_time(pd.Timestamp(parsed).time())
    try:
        parsed_time = time.fromisoformat(text)
    except ValueError:
        parsed_time = None
    if parsed_time is not None:
        return _timestamp_from_time(parsed_time)
    for candidate_format in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M", "%H%M%S", "%H%M"):
        parsed = pd.to_datetime(text, format=candidate_format, errors="coerce")
        if not pd.isna(parsed):
            return _timestamp_from_time(pd.Timestamp(parsed).time())
    return None


def parse_datetime_value(value: Any) -> pd.Timestamp | None:
    if cell_state(value) != STATE_VALUE:
        return None

    if isinstance(value, pd.Timestamp):
        timestamp = value
    elif isinstance(value, datetime):
        timestamp = pd.Timestamp(value)
    elif isinstance(value, date):
        timestamp = pd.Timestamp(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        timestamp = pd.to_datetime(text, errors="coerce")
    else:
        return None

    if pd.isna(timestamp):
        return None
    if not isinstance(timestamp, pd.Timestamp):
        timestamp = pd.Timestamp(timestamp)
    return timestamp


def _temporal_format(tags: tuple[str, ...] | list[str]) -> str | None:
    for tag in tags:
        text = str(tag).strip()
        upper = text.upper()
        for prefix in ("DATE(", "DATETIME(", "TIME("):
            if upper.startswith(prefix) and text.endswith(")"):
                return text[len(prefix) : -1]
    return None


def _has_tag(tags: tuple[str, ...] | list[str], tag: str) -> bool:
    target = tag.upper()
    return any(str(item).strip().upper().split("(", 1)[0] == target for item in tags)


def _timestamp_from_time(value: time) -> pd.Timestamp:
    return pd.Timestamp.combine(_TIME_ANCHOR_DATE, value)
