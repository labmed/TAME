"""Opt-in, versioned bindings for released values and left-censoring codes."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import ColumnSpec, TameDataset, ValidationIssue


def column_ids(dataset: TameDataset) -> dict[str, ColumnSpec]:
    result = {}
    for column in dataset.columns:
        identifier = ci_get(dataset.column_metadata(column), "ID", None)
        if identifier is None:
            continue
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"COLUMN.{column.name}.ID must be a nonempty string")
        if identifier in result:
            raise ValueError(f"Duplicate COLUMN.ID: {identifier}")
        result[identifier] = column
    return result


def resolve_id(dataset: TameDataset, identifier: str) -> ColumnSpec:
    columns = column_ids(dataset)
    if not isinstance(identifier, str) or identifier not in columns:
        raise ValueError(f"Unknown COLUMN.ID: {identifier}")
    return columns[identifier]


def check_contract_version(dataset: TameDataset) -> None:
    section = ci_get(dataset.meta, "ANALYSIS_CONTRACT", {})
    declared = ci_get(section, "VERSION", None) if isinstance(section, dict) else None
    if isinstance(declared, bool) or not isinstance(declared, int) or declared != 1:
        raise ValueError("ANALYSIS_CONTRACT.VERSION = 1 is required for analysis bindings")


def strict_numbers(values: pd.Series, label: str) -> pd.Series:
    present = values.map(lambda value: cell_state(value) == STATE_VALUE)
    result = pd.to_numeric(values.where(present, np.nan), errors="coerce").astype(float)
    if (present & (~np.isfinite(result))).any():
        raise ValueError(f"{label}: nonnumeric or nonfinite value")
    return result


def censoring_components(dataset: TameDataset, column: ColumnSpec):
    check_contract_version(dataset)
    config = ci_get(dataset.column_metadata(column), "CENSORING", None)
    if not isinstance(config, dict):
        raise ValueError(f"{column.name}: CENSORING binding is required")
    allowed = {"SOURCE_ID", "FLAG_ID", "LIMIT", "BELOW_CODE", "OBSERVED_CODE"}
    if {str(k).upper() for k in config} != allowed:
        raise ValueError(f"{column.name}: CENSORING requires exactly {sorted(allowed)}")
    source = resolve_id(dataset, ci_get(config, "SOURCE_ID"))
    flag = resolve_id(dataset, ci_get(config, "FLAG_ID"))
    if len({source.name, flag.name, column.name}) != 3:
        raise ValueError("Censoring source, flag and derived columns must be distinct")
    if not dataset.column_has_tag(source, "NUM") or not dataset.column_has_tag(column, "<NUM>"):
        raise ValueError("Censoring requires a NUM source and a <NUM> derived column")
    source_unit = ci_get(dataset.column_metadata(source), "UNIT", None)
    target_unit = ci_get(dataset.column_metadata(column), "UNIT", None)
    if not source_unit or source_unit != target_unit:
        raise ValueError("Censoring source and derived UNIT must be declared and identical")
    try:
        limit = float(ci_get(config, "LIMIT"))
        below = float(ci_get(config, "BELOW_CODE"))
        observed = float(ci_get(config, "OBSERVED_CODE"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Censoring limit and codes must be numeric") from exc
    if not all(math.isfinite(v) for v in (limit, below, observed)) or limit <= 0 or below == observed:
        raise ValueError("Censoring requires a positive finite limit and distinct finite codes")
    raw = strict_numbers(dataset.df[source.name], source.name)
    flags = strict_numbers(dataset.df[flag.name], flag.name)
    if not raw.isna().equals(flags.isna()):
        raise ValueError(f"{column.name}: source and flag missingness differ")
    if not flags.dropna().isin([below, observed]).all():
        raise ValueError(f"{column.name}: unknown detection-limit code")
    if (raw[flags.eq(observed)] < limit).any():
        raise ValueError(f"{column.name}: observed code conflicts with the detection limit")
    return source, raw, flags.eq(below), limit


def derive_censored_results(dataset: TameDataset) -> TameDataset:
    """Preserve released cells; populate declared derived columns non-destructively."""
    check_contract_version(dataset)
    column_ids(dataset)
    frame = dataset.df.copy()
    for column in dataset.columns:
        if ci_get(dataset.column_metadata(column), "CENSORING", None) is None:
            continue
        source, _, below, limit = censoring_components(dataset, column)
        derived = dataset.df[source.name].copy()
        derived.loc[below] = "<" + np.format_float_positional(limit, trim="-")
        frame[column.name] = derived
    return dataset.replace(df=frame, raw_sections={})


def check_censoring_values(dataset: TameDataset, column: ColumnSpec) -> None:
    source, raw, below, limit = censoring_components(dataset, column)
    from .analysis import parse_comparator_number

    for position, value in enumerate(dataset.df[column.name]):
        original = dataset.df[source.name].iloc[position]
        if pd.isna(raw.iloc[position]):
            valid = cell_state(value) == cell_state(original)
        else:
            parsed = parse_comparator_number(str(value).strip()) if cell_state(value) == STATE_VALUE else None
            valid = parsed is not None and (
                (parsed[0] == "<" and parsed[1] == limit) if below.iloc[position]
                else (parsed[0] in ("", "=") and parsed[1] == raw.iloc[position])
            )
        if not valid:
            raise ValueError(f"{column.name}: derived value contradicts source/flag at row {position + 1}")


def contract_validation_issues(dataset: TameDataset) -> list[ValidationIssue]:
    issues = []
    try:
        column_ids(dataset)
        if ci_get(dataset.meta, "ANALYSIS_CONTRACT", None) is not None:
            check_contract_version(dataset)
    except ValueError as exc:
        issues.append(ValidationIssue(0, "META", "ANALYSIS_CONTRACT", None, str(exc)))
    for column in dataset.columns:
        if ci_get(dataset.column_metadata(column), "CENSORING_INTERVAL", None) is not None:
            try:
                from .interval_censoring import intervals
                intervals(dataset, column)
            except ValueError as exc:
                issues.append(ValidationIssue(0, column.name, "CENSORING_INTERVAL", None, str(exc)))
        if ci_get(dataset.column_metadata(column), "CENSORING", None) is not None:
            try:
                check_censoring_values(dataset, column)
            except ValueError as exc:
                issues.append(ValidationIssue(0, column.name, "CENSORING", None, str(exc)))
    return issues


def measurement_values(dataset: TameDataset, column: ColumnSpec, policy: str) -> tuple[pd.Series, pd.Series]:
    """Return explicitly selected analysis values and a below-limit mask."""
    from .measurement_tags import require_measurement_tags, require_quantitative
    from .observation_contract import require_capabilities
    require_measurement_tags(dataset)
    require_quantitative(dataset, column)
    require_capabilities(dataset)
    if ci_get(dataset.column_metadata(column), "CENSORING_INTERVAL", None) is not None:
        from .interval_censoring import interval_values
        return interval_values(dataset, column, policy)
    if policy == "MIDPOINT":
        raise ValueError("MIDPOINT requires CENSORING_INTERVAL; do not guess a censoring interval")
    if policy not in {"RELEASED", "VALUE", "DELETE"}:
        raise ValueError("Policy must be RELEASED, VALUE or DELETE")
    binding = ci_get(dataset.column_metadata(column), "CENSORING", None)
    if binding is not None:
        check_censoring_values(dataset, column)
        _, raw, below, limit = censoring_components(dataset, column)
        values = raw.copy()
        if policy != "RELEASED":
            values.loc[below] = limit if policy == "VALUE" else np.nan
        return values, below
    # Without a released flag there is no basis for inferring censoring from small numbers.
    values = strict_numbers(dataset.df[column.name], column.name)
    return values, pd.Series(False, index=dataset.df.index)
