from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Iterable

import pandas as pd

from .cellstate import STATE_VALUE, cell_state
from .models import TameDataset


SEX_CANONICAL_VALUES = ("male", "female", "other", "unknown")

SEX_TOKEN_MAP = {
    "M": "male",
    "MALE": "male",
    "MAN": "male",
    "BOY": "male",
    "남": "male",
    "남자": "male",
    "남성": "male",
    "남아": "male",
    "F": "female",
    "FEMALE": "female",
    "WOMAN": "female",
    "GIRL": "female",
    "여": "female",
    "여자": "female",
    "여성": "female",
    "여아": "female",
    "O": "other",
    "OTHER": "other",
    "X": "other",
    "NB": "other",
    "NONBINARY": "other",
    "NON-BINARY": "other",
    "NON_BINARY": "other",
    "기타": "other",
    "그외": "other",
    "U": "unknown",
    "UNK": "unknown",
    "UNKNOWN": "unknown",
    "NA": "unknown",
    "N/A": "unknown",
    "미상": "unknown",
    "모름": "unknown",
    "불명": "unknown",
}

SEX_BINARY_CODES = {"0", "1"}


def normalize_sex(value: Any, *, binary_map: Mapping[str, str] | None = None) -> str | None:
    if cell_state(value) != STATE_VALUE:
        return None

    raw_key = _sex_value_key(value)
    binary = canonical_sex_binary_map(binary_map)
    if raw_key in binary:
        return binary[raw_key]

    raw_upper = raw_key.upper()
    compact_upper = _compact_key(raw_upper)
    return SEX_TOKEN_MAP.get(raw_upper) or SEX_TOKEN_MAP.get(compact_upper)


def canonical_sex_binary_map(mapping: Mapping[str, str] | None) -> dict[str, str]:
    if not isinstance(mapping, Mapping):
        return {}

    canonical: dict[str, str] = {}
    for key, value in mapping.items():
        code = _sex_value_key(key)
        if code not in SEX_BINARY_CODES:
            continue
        normalized = normalize_sex(value)
        if normalized in SEX_CANONICAL_VALUES:
            canonical[code] = normalized
    return canonical


def parse_sex_binary_map(assignments: Iterable[str] | None) -> dict[str, str]:
    binary_map: dict[str, str] = {}
    for assignment in assignments or ():
        text = str(assignment).strip()
        if "=" not in text:
            raise ValueError(f"Expected SEX code mapping as CODE=VALUE, got: {assignment}")
        raw_code, raw_value = text.split("=", 1)
        code = _sex_value_key(raw_code)
        if code not in SEX_BINARY_CODES:
            raise ValueError(f"SEX binary code must be 0 or 1, got: {raw_code}")
        canonical = normalize_sex(raw_value)
        if canonical not in SEX_CANONICAL_VALUES:
            allowed = ", ".join(SEX_CANONICAL_VALUES)
            raise ValueError(f"SEX mapping value must be one of {allowed}, got: {raw_value}")
        binary_map[code] = canonical
    return binary_map


def standardize_sex_dataset(
    dataset: TameDataset,
    *,
    binary_map: Mapping[str, str] | None = None,
) -> TameDataset:
    sex_columns = dataset.columns_with_tag("SEX")
    if not sex_columns:
        return dataset

    frame = dataset.df.copy()
    changed = False
    for column in sex_columns:
        standardized = frame[column.name].map(lambda value: _standardize_value(value, binary_map=binary_map))
        if not standardized.equals(frame[column.name]):
            frame[column.name] = standardized
            changed = True

    return dataset.replace(df=frame) if changed else dataset


def sex_value_profile(dataset: TameDataset, *, binary_map: Mapping[str, str] | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in dataset.columns_with_tag("SEX"):
        values = dataset.df[column.name]
        value_counts: dict[str, int] = {}
        for value in values:
            if cell_state(value) != STATE_VALUE:
                continue
            raw_value = _sex_value_key(value)
            value_counts[raw_value] = value_counts.get(raw_value, 0) + 1

        for raw_value, raw_count in value_counts.items():
            canonical = normalize_sex(raw_value, binary_map=binary_map)
            rows.append(
                {
                    "column": column.name,
                    "tags": "::".join(column.tags),
                    "raw_value": raw_value,
                    "raw_count": int(raw_count),
                    "canonical_value": canonical,
                    "recognized": canonical is not None,
                    "will_change": canonical is not None and raw_value != canonical,
                }
            )

    columns = [
        "column",
        "tags",
        "raw_value",
        "raw_count",
        "canonical_value",
        "canonical_count",
        "recognized",
        "will_change",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    frame = pd.DataFrame(rows)
    frame["canonical_count"] = 0
    recognized = frame.loc[frame["recognized"]].copy()
    if not recognized.empty:
        totals = recognized.groupby(["column", "canonical_value"], dropna=False, observed=False)["raw_count"].sum().to_dict()
        frame["canonical_count"] = frame.apply(
            lambda row: int(totals.get((row["column"], row["canonical_value"]), 0)),
            axis=1,
        )

    return frame[columns].sort_values(
        ["column", "recognized", "canonical_value", "raw_value"],
        ascending=[True, False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def _standardize_value(value: Any, *, binary_map: Mapping[str, str] | None) -> Any:
    normalized = normalize_sex(value, binary_map=binary_map)
    return normalized if normalized is not None else value


def _sex_value_key(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).strip()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))

    text = str(value).strip()
    if re.fullmatch(r"[+-]?\d+\.0+", text):
        return str(int(float(text)))
    return text


def _compact_key(text: str) -> str:
    return re.sub(r"[\s_-]+", "", text)
