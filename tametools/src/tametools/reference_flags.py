"""Result-bound reference intervals and interval-aware interpretation, not substitution."""
import math

import numpy as np
import pandas as pd

from .analysis_contract import resolve_id, strict_numbers
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .measurement_tags import require_measurement_tags, require_quantitative


def interval_flag(value, low, high):
    from .analysis import parse_comparator_number
    if cell_state(value) != STATE_VALUE:
        return "NE", "RESULT_MISSING"
    parsed = parse_comparator_number(str(value).strip())
    if parsed is None or not math.isfinite(parsed[1]):
        raise ValueError("Reference interpretation requires finite numeric/comparator results")
    low = float(low) if pd.notna(low) else -np.inf
    high = float(high) if pd.notna(high) else np.inf
    if low == -np.inf and high == np.inf:
        return "NE", "REFERENCE_MISSING"
    if low > high:
        raise ValueError("Reference lower bound exceeds upper bound")
    operator, number = parsed
    lower = -np.inf if operator in {"<", "<="} else number
    upper = np.inf if operator in {">", ">="} else number
    if upper < low or (operator == "<" and upper == low):
        return "L", "BELOW_REFERENCE"
    if lower > high or (operator == ">" and lower == high):
        return "H", "ABOVE_REFERENCE"
    if lower >= low and upper <= high:
        return "N", "WITHIN_REFERENCE"
    return "IND", "CENSORED_INTERVAL_OVERLAPS_REFERENCE"


def _bound(dataset, config, field, unit):
    constant, identifier = ci_get(config, field, None), ci_get(config, field + "_ID", None)
    if constant is not None and identifier is not None:
        raise ValueError("Declare " + field + " or " + field + "_ID, not both")
    if identifier is not None:
        column = resolve_id(dataset, identifier)
        require_quantitative(dataset, column, require_unit=True)
        if ci_get(dataset.column_metadata(column), "UNIT", None) != unit:
            raise ValueError("Reference bound UNIT conflicts with result UNIT")
        return strict_numbers(dataset.df[column.name], field)
    if constant is None:
        return pd.Series(np.nan, index=dataset.df.index)
    if isinstance(constant, bool) or not isinstance(constant, (int, float)) or not math.isfinite(constant):
        raise ValueError("Reference bounds must be finite TOML numbers")
    return pd.Series(float(constant), index=dataset.df.index)


def reference_flags(dataset, column, *, allow_undeclared_units=False):
    if type(allow_undeclared_units) is not bool:
        raise ValueError("ALLOW_UNDECLARED_UNITS must be boolean")
    require_measurement_tags(dataset)
    from .observation_contract import require_capabilities, result_status
    require_capabilities(dataset)
    accepted, status_reasons = result_status(dataset, column)
    require_quantitative(dataset, column)
    metadata = dataset.column_metadata(column)
    unit = ci_get(metadata, "UNIT", None)
    config = ci_get(metadata, "REFERENCE_INTERVAL", None)
    warnings = []
    if config is not None:
        allowed = {"LOW", "HIGH", "LOW_ID", "HIGH_ID", "UNIT", "POPULATION", "REFERENCE", "APPLIES_WHEN", "TIME_ID", "VALID_FROM", "VALID_TO"}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("Invalid REFERENCE_INTERVAL fields")
        if not unit or ci_get(config, "UNIT", unit) != unit:
            raise ValueError("REFERENCE_INTERVAL requires a matching result UNIT")
        low, high = [_bound(dataset, config, field, unit) for field in ("LOW", "HIGH")]
    else:
        context = ci_get(metadata, "PIVOT_CONTEXT", {})
        if isinstance(context, dict) and any(key in context for key in ("REF_LOW", "REF_HIGH")):
            bound_unit = ci_get(context, "UNIT", unit)
            unit = unit or bound_unit
            if unit and bound_unit and unit != bound_unit:
                raise ValueError("PIVOT_CONTEXT reference UNIT conflicts with result UNIT")
            config = {"LOW": ci_get(context, "REF_LOW", None), "HIGH": ci_get(context, "REF_HIGH", None)}
            from .units import decimal_number
            config = {key: float(decimal_number(value, label="PIVOT_CONTEXT reference"))
                      if cell_state(value) == STATE_VALUE else None for key, value in config.items()}
            low, high = [_bound(dataset, config, field, unit) for field in ("LOW", "HIGH")]
        else:
            bounds = []
            for tag in ("REF_LOW", "REF_HIGH"):
                matches = dataset.columns_with_tag(tag)
                if len(matches) > 1:
                    raise ValueError("Ambiguous " + tag + "; bind references through COLUMN.REFERENCE_INTERVAL")
                bounds.append(matches[0] if matches else None)
            if any(bounds) and len([c for c in dataset.columns_with_tag("RESULT") if not dataset.column_has_any_tag(c, ["NOMINAL", "ORDINAL"])]) > 1:
                raise ValueError("Multiple results require result-specific REFERENCE_INTERVAL bindings")
            values = []
            for bound in bounds:
                if bound is None:
                    values.append(pd.Series(np.nan, index=dataset.df.index))
                    continue
                bound_unit = ci_get(dataset.column_metadata(bound), "UNIT", None)
                if unit and bound_unit and unit != bound_unit:
                    raise ValueError("Reference bound UNIT conflicts with result UNIT")
                if not bound_unit and not allow_undeclared_units:
                    raise ValueError("Reference columns require declared UNIT or explicit ALLOW_UNDECLARED_UNITS")
                if not bound_unit and allow_undeclared_units:
                    warnings.append("Explicit legacy mode: reference unit compatibility was not verified")
                require_quantitative(dataset, bound)
                values.append(strict_numbers(dataset.df[bound.name], bound.name))
            low, high = values
            config = {}
    if not unit:
        if not allow_undeclared_units:
            raise ValueError("Reference interpretation requires result UNIT or explicit ALLOW_UNDECLARED_UNITS")
        warnings.append("Explicit legacy mode: unit compatibility was not verified")
    eligible = np.ones(len(dataset.df), dtype=bool)
    missing = np.zeros(len(dataset.df), dtype=bool)
    rule = ci_get(config, "APPLIES_WHEN", None)
    if rule is not None:
        from .planned_analysis import _conditions
        if not isinstance(rule, dict) or set(rule) != {"ALL_OF"}:
            raise ValueError("Reference APPLIES_WHEN requires ALL_OF")
        eligible, missing = _conditions(dataset, rule["ALL_OF"])
    validity = [ci_get(config, key, None) for key in ("TIME_ID", "VALID_FROM", "VALID_TO")]
    time_missing = np.zeros(len(dataset.df), dtype=bool)
    if any(value is not None for value in validity):
        if any(value is None for value in validity):
            raise ValueError("Reference validity requires TIME_ID, VALID_FROM, VALID_TO")
        from .observation_contract import timestamp_series
        start, end = [pd.Timestamp(value) for value in validity[1:]]
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("Reference validity bounds require timezone offsets and increasing bounds")
        times = timestamp_series(dataset.df[resolve_id(dataset, validity[0]).name])
        time_missing = times.isna().to_numpy()
        eligible &= times.ge(start).to_numpy() & times.lt(end).to_numpy()
    flags, reasons = [], []
    interval_results = None
    if ci_get(metadata, "CENSORING_INTERVAL", None) is not None:
        from .interval_censoring import interval_reference_flags
        interval_results = interval_reference_flags(dataset, column, low, high)
    for position, (value, lo, hi) in enumerate(zip(dataset.df[column.name], low, high)):
        if not accepted.iloc[position]:
            flag, reason = "NE", status_reasons.iloc[position]
        elif missing[position] or time_missing[position]:
            flag, reason = "NE", "REFERENCE_CONTEXT_MISSING"
        elif not eligible[position]:
            flag, reason = "NE", "REFERENCE_NOT_APPLICABLE"
        elif interval_results is not None:
            flag, reason = interval_results[position]
        else:
            flag, reason = interval_flag(value, lo, hi)
        flags.append(flag)
        reasons.append(reason)
    return pd.Series(flags, index=dataset.df.index), pd.Series(reasons, index=dataset.df.index), warnings
