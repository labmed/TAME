from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

import pandas as pd

from .cellstate import STATE_VALUE, cell_state
from .models import TameDataset
from .tag_catalog import tag_property_int


AGE_UCUM_SYSTEM = "http://unitsofmeasure.org"
AGE_DEFAULT_UNIT = "a"

AGE_CANONICAL_UNITS = {
    "a": {"label": "year", "years": 1.0},
    "mo": {"label": "month", "years": 1.0 / 12.0},
    "d": {"label": "day", "years": 1.0 / 365.25},
}

AGE_UNIT_ALIASES = {
    "": "a",
    "a": "a",
    "ann": "a",
    "y": "a",
    "yr": "a",
    "yrs": "a",
    "year": "a",
    "years": "a",
    "세": "a",
    "년": "a",
    "mo": "mo",
    "mon": "mo",
    "mons": "mo",
    "month": "mo",
    "months": "mo",
    "m": "mo",
    "개월": "mo",
    "월": "mo",
    "d": "d",
    "day": "d",
    "days": "d",
    "일": "d",
}

AGE_VALUE_RE = re.compile(
    r"^\s*(?P<number>[+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>[A-Za-z가-힣]*)\s*$"
)


@dataclass(frozen=True)
class AgeValue:
    raw_value: str
    number: float
    unit_code: str
    unit_label: str
    years: float
    hl7_value: str
    canonical_value: str
    will_change: bool


def parse_age(value: Any) -> AgeValue | None:
    if cell_state(value) != STATE_VALUE:
        return None

    raw_value = str(value).strip()
    match = AGE_VALUE_RE.match(raw_value)
    if not match:
        return None

    try:
        number = float(match.group("number"))
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None

    unit_token = match.group("unit").strip()
    unit_code = AGE_UNIT_ALIASES.get(unit_token.lower())
    if unit_code is None:
        return None

    unit = AGE_CANONICAL_UNITS[unit_code]
    formatted_number = _format_age_number(number)
    hl7_value = f"{formatted_number}{unit_code}"
    canonical_value = formatted_number if unit_code == AGE_DEFAULT_UNIT else f"{formatted_number}{unit_code}"
    return AgeValue(
        raw_value=raw_value,
        number=number,
        unit_code=unit_code,
        unit_label=str(unit["label"]),
        years=number * float(unit["years"]),
        hl7_value=hl7_value,
        canonical_value=canonical_value,
        will_change=raw_value != canonical_value,
    )


def parse_age_to_years(value: Any) -> float | None:
    parsed = parse_age(value)
    return None if parsed is None else parsed.years


def standardize_age_value(value: Any) -> Any:
    parsed = parse_age(value)
    if parsed is None:
        return value
    return parsed.canonical_value


def standardize_age_dataset(dataset: TameDataset) -> TameDataset:
    age_columns = dataset.columns_with_tag("AGE")
    if not age_columns:
        return dataset

    frame = dataset.df.copy()
    changed = False
    for column in age_columns:
        standardized = frame[column.name].map(standardize_age_value)
        if not standardized.equals(frame[column.name]):
            frame[column.name] = standardized
            changed = True

    return dataset.replace(df=frame) if changed else dataset


def age_bin_width_for_column(dataset: TameDataset, column, default: int = 10) -> int:
    return tag_property_int(dataset.meta, column.tags, "AGE_BIN_WIDTH", default)


def age_band_label(age_years: float | None, *, width: int = 10, open_upper: int = 70) -> str | None:
    if age_years is None or pd.isna(age_years):
        return None
    if width <= 0:
        raise ValueError("AGE band width must be positive.")

    age = float(age_years)
    if age < 0:
        return None
    if age < 1:
        return "<1"
    if age >= open_upper:
        return f"{open_upper}+"

    lower = int(age // width) * width
    if lower == 0:
        lower = 1
    upper = min((int(age // width) + 1) * width - 1, open_upper - 1)
    return f"{lower}-{upper}"


def age_band_bounds(label: Any) -> tuple[float | None, float | None] | None:
    if label in (None, "", "ALL"):
        return None
    text = str(label)
    if text == "<1":
        return (0.0, 1.0)
    if text.endswith("+"):
        try:
            return (float(text[:-1]), None)
        except ValueError:
            return None
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    try:
        return (float(left), float(right))
    except ValueError:
        return None


def age_value_profile(dataset: TameDataset) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in dataset.columns_with_tag("AGE"):
        preferred_width = age_bin_width_for_column(dataset, column, default=10)

        value_counts: dict[str, int] = {}
        for value in dataset.df[column.name]:
            if cell_state(value) != STATE_VALUE:
                continue
            raw_value = str(value).strip()
            value_counts[raw_value] = value_counts.get(raw_value, 0) + 1

        for raw_value, raw_count in value_counts.items():
            parsed = parse_age(raw_value)
            rows.append(
                {
                    "column": column.name,
                    "tags": "::".join(column.tags),
                    "raw_value": raw_value,
                    "raw_count": int(raw_count),
                    "canonical_value": None if parsed is None else parsed.canonical_value,
                    "canonical_count": 0,
                    "recognized": parsed is not None,
                    "will_change": False if parsed is None else parsed.will_change,
                    "unit_code": None if parsed is None else parsed.unit_code,
                    "unit_label": None if parsed is None else parsed.unit_label,
                    "years": None if parsed is None else parsed.years,
                    "preferred_band": None if parsed is None else age_band_label(parsed.years, width=preferred_width),
                    "preferred_band_width": preferred_width,
                    "band_5": None if parsed is None else age_band_label(parsed.years, width=5),
                    "band_10": None if parsed is None else age_band_label(parsed.years, width=10),
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
        "unit_code",
        "unit_label",
        "years",
        "preferred_band",
        "preferred_band_width",
        "band_5",
        "band_10",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    frame = pd.DataFrame(rows)
    recognized = frame.loc[frame["recognized"]].copy()
    if not recognized.empty:
        totals = recognized.groupby(["column", "canonical_value"], dropna=False, observed=False)["raw_count"].sum().to_dict()
        frame["canonical_count"] = frame.apply(
            lambda row: int(totals.get((row["column"], row["canonical_value"]), 0)),
            axis=1,
        )

    unit_order = {unit_code: index for index, unit_code in enumerate(AGE_CANONICAL_UNITS)}
    frame["_unit_order"] = frame["unit_code"].map(lambda value: unit_order.get(value, len(unit_order)))
    return frame[columns + ["_unit_order"]].sort_values(
        ["column", "recognized", "_unit_order", "years", "raw_value"],
        ascending=[True, False, True, True, True],
        kind="stable",
    )[columns].reset_index(drop=True)


def _format_age_number(number: float) -> str:
    return f"{number:g}"
