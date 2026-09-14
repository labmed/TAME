"""Per-row left/right/closed-interval censoring without inventing concentrations."""
import numpy as np
import pandas as pd

from .analysis_contract import resolve_id, strict_numbers
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .measurement_tags import require_quantitative


def intervals(dataset, column):
    config = ci_get(dataset.column_metadata(column), "CENSORING_INTERVAL", None)
    required = {"VERSION", "RELEASED_ID", "KIND_ID", "LOWER_ID", "UPPER_ID", "REFERENCE"}
    if not isinstance(config, dict) or set(config) != required or type(config["VERSION"]) is not int or config["VERSION"] != 1:
        raise ValueError("CENSORING_INTERVAL requires VERSION=1 and released/kind/lower/upper bindings and REFERENCE")
    if not isinstance(config["REFERENCE"], str) or not config["REFERENCE"].strip():
        raise ValueError("Censoring requires a source REFERENCE")
    if ci_get(dataset.column_metadata(column), "CENSORING", None) is not None:
        raise ValueError("CENSORING and CENSORING_INTERVAL are mutually exclusive")
    bindings = {key: resolve_id(dataset, config[key]) for key in ("RELEASED_ID", "KIND_ID", "LOWER_ID", "UPPER_ID")}
    if len({column.name, *(c.name for c in bindings.values())}) != 5:
        raise ValueError("Interval-censoring bindings and result must be distinct")
    unit = ci_get(dataset.column_metadata(column), "UNIT", None)
    require_quantitative(dataset, column, require_unit=True)
    for key in ("RELEASED_ID", "LOWER_ID", "UPPER_ID"):
        bound = bindings[key]
        require_quantitative(dataset, bound, require_unit=True)
        if ci_get(dataset.column_metadata(bound), "UNIT", None) != unit:
            raise ValueError("Interval-censoring bound/released/result UNIT conflict")
    raw = strict_numbers(dataset.df[bindings["RELEASED_ID"].name], "Released concentration")
    result = strict_numbers(dataset.df[column.name], "Interval result (released value)")
    if not np.all((raw.eq(result) | (raw.isna() & result.isna())).to_numpy()):
        raise ValueError("Interval result must preserve the declared released value")
    kinds = dataset.df[bindings["KIND_ID"].name]
    lower = strict_numbers(dataset.df[bindings["LOWER_ID"].name], "Censoring lower bound")
    upper = strict_numbers(dataset.df[bindings["UPPER_ID"].name], "Censoring upper bound")
    for position, (kind, value, low, high) in enumerate(zip(kinds, raw, lower, upper)):
        absent = cell_state(kind) != STATE_VALUE
        if absent:
            if any(pd.notna(v) for v in (value, low, high)):
                raise ValueError("Missing censoring kind with nonmissing values/bounds")
            continue
        if kind not in {"EXACT", "LEFT", "LEFT_CLOSED", "RIGHT", "RIGHT_CLOSED", "INTERVAL"}:
            raise ValueError("Unknown censoring kind; use EXACT/LEFT/RIGHT/INTERVAL")
        if kind == "EXACT":
            if pd.isna(value) or pd.notna(low) or pd.notna(high):
                raise ValueError("EXACT needs a released value and absent interval bounds")
        elif kind in {"LEFT", "LEFT_CLOSED"} and (pd.notna(low) or pd.isna(high)):
            raise ValueError("LEFT needs only a finite upper bound (strict <)")
        elif kind in {"RIGHT", "RIGHT_CLOSED"} and (pd.isna(low) or pd.notna(high)):
            raise ValueError("RIGHT needs only a finite lower bound (strict >)")
        elif kind == "INTERVAL" and (pd.isna(low) or pd.isna(high) or low >= high):
            raise ValueError("INTERVAL needs increasing finite closed bounds")
    return raw, kinds, lower, upper


def interval_values(dataset, column, policy):
    raw, kinds, lower, upper = intervals(dataset, column)
    censored = kinds.isin(["LEFT", "LEFT_CLOSED", "RIGHT", "RIGHT_CLOSED", "INTERVAL"])
    values = raw.copy()
    if policy == "DELETE":
        values.loc[censored] = np.nan
    elif policy in {"VALUE", "MIDPOINT"}:
        if policy == "VALUE" and kinds.eq("INTERVAL").any():
            raise ValueError("VALUE is ambiguous for interval censoring; explicitly choose MIDPOINT, RELEASED or DELETE")
        values.loc[kinds.isin(["LEFT", "LEFT_CLOSED"])] = upper
        values.loc[kinds.isin(["RIGHT", "RIGHT_CLOSED"])] = lower
        values.loc[kinds.eq("INTERVAL")] = (lower + upper) / 2
    elif policy != "RELEASED":
        raise ValueError("Unsupported interval-censoring policy")
    return values, censored


def interval_reference_flags(dataset, column, lows, highs):
    from .reference_flags import interval_flag
    raw, kinds, lower, upper = intervals(dataset, column)
    results = []
    for value, kind, lo, hi, ref_lo, ref_hi in zip(raw, kinds, lower, upper, lows, highs):
        if kind in {"LEFT", "LEFT_CLOSED"}:
            results.append(interval_flag(("<" if kind == "LEFT" else "<=") + str(hi), ref_lo, ref_hi))
        elif kind in {"RIGHT", "RIGHT_CLOSED"}:
            results.append(interval_flag((">" if kind == "RIGHT" else ">=") + str(lo), ref_lo, ref_hi))
        elif kind == "INTERVAL":
            if pd.isna(ref_lo) and pd.isna(ref_hi):
                results.append(("NE", "REFERENCE_MISSING"))
                continue
            ref_lo = -np.inf if pd.isna(ref_lo) else ref_lo
            ref_hi = np.inf if pd.isna(ref_hi) else ref_hi
            if ref_lo > ref_hi:
                raise ValueError("Reference lower bound exceeds upper bound")
            flag = "L" if hi < ref_lo else "H" if lo > ref_hi else "N" if lo >= ref_lo and hi <= ref_hi else "IND"
            results.append((flag, "CENSORED_INTERVAL_" + flag))
        else:
            results.append(interval_flag(value, ref_lo, ref_hi))
    return results
