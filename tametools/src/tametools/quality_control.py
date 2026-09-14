"""Run-aware basic QC rules against an externally declared, dated baseline."""
import numpy as np
import pandas as pd

from .analysis import numeric_series_for
from .analysis_contract import resolve_id
from .config import ci_get
from .measurement_tags import require_quantitative
from .observation_contract import key_frame, timestamp_series


def evaluate_qc(dataset, results, options):
    bindings = {}
    for field in ("RUN_ID", "TIME_ID", "LEVEL_ID", "LOT_ID", "INSTRUMENT_ID"):
        identifier = ci_get(options, field, None)
        if identifier is None:
            raise ValueError("WESTGARD requires " + field)
        bindings[field] = resolve_id(dataset, identifier).name
    work = key_frame(dataset, [ci_get(options, key) for key in bindings]).rename(columns={value: key for key, value in bindings.items()})
    work["TIME_ID"] = timestamp_series(dataset.df[bindings["TIME_ID"]])
    keys = ["INSTRUMENT_ID", "LOT_ID", "RUN_ID"]
    if work.duplicated(keys + ["LEVEL_ID"]).any():
        raise ValueError("Duplicate QC run/level; define separate runs or resolve replicates explicitly")
    if work.duplicated(["INSTRUMENT_ID", "LOT_ID", "LEVEL_ID", "TIME_ID"]).any():
        raise ValueError("Ambiguous QC run ordering: repeated level timestamps")
    if work.groupby(keys).TIME_ID.nunique().gt(1).any():
        raise ValueError("All levels in a run must share the declared run timestamp")
    baselines = ci_get(options, "BASELINES", None)
    if not isinstance(baselines, list) or not baselines:
        raise ValueError("WESTGARD requires reviewed BASELINES; use LEVEY_JENNINGS for retrospective exploration")
    required = {"RESULT_ID", "LEVEL", "LOT", "INSTRUMENT", "MEAN", "SD", "UNIT", "REFERENCE", "VALID_FROM", "VALID_TO"}
    prepared = []
    for baseline in baselines:
        if not isinstance(baseline, dict) or set(baseline) != required:
            raise ValueError("Invalid QC baseline fields")
        for field in ("MEAN", "SD"):
            value = baseline[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                raise ValueError("QC mean/SD must be finite numbers")
        if baseline["SD"] <= 0 or any(not isinstance(baseline[key], str) or not baseline[key].strip() for key in required - {"MEAN", "SD"}):
            raise ValueError("QC baseline needs positive SD and nonempty context/reference strings")
        start, end = timestamp_series(pd.Series([baseline["VALID_FROM"], baseline["VALID_TO"]]))
        if start >= end:
            raise ValueError("QC baseline validity bounds must increase")
        resolve_id(dataset, baseline["RESULT_ID"])
        prepared.append((baseline, start, end))
    rows = []
    for result in results:
        require_quantitative(dataset, result, require_unit=True)
        identifier = ci_get(dataset.column_metadata(result), "ID", None)
        unit = ci_get(dataset.column_metadata(result), "UNIT")
        values = numeric_series_for(dataset, result, crr_policy="DELETE")
        data = work.copy()
        data["value"] = values
        data["baseline_index"] = -1
        data["z"] = np.nan
        for index, row in data.iterrows():
            matches = [(i, baseline) for i, (baseline, start, end) in enumerate(prepared)
                if baseline["RESULT_ID"] == identifier and baseline["LEVEL"] == row.LEVEL_ID
                and baseline["LOT"] == row.LOT_ID and baseline["INSTRUMENT"] == row.INSTRUMENT_ID
                and start <= row.TIME_ID < end]
            if len(matches) != 1:
                raise ValueError("Every QC point requires exactly one applicable baseline (including missing results)")
            number, baseline = matches[0]
            if baseline["UNIT"] != unit:
                raise ValueError("QC baseline/result UNIT conflict")
            data.loc[index, "baseline_index"] = number
            data.loc[index, "z"] = (row.value - baseline["MEAN"]) / baseline["SD"]
        data = data.sort_values(["TIME_ID", "INSTRUMENT_ID", "LOT_ID", "RUN_ID", "LEVEL_ID"], kind="stable")
        flags = {index: [] for index in data.index}
        for index, z in data.z.items():
            if abs(z) > 3:
                flags[index].append("1_3s")
        for _, group in data.groupby(keys, sort=False):
            finite = group.z.dropna()
            if finite.gt(2).any() and finite.lt(-2).any():
                for index in finite.index:
                    flags[index].append("R_4s")
            for condition in (finite.gt(2), finite.lt(-2)):
                if condition.sum() >= 2:
                    for index in condition[condition].index:
                        flags[index].append("2_2s")
        # Consecutive runs at the same level and baseline. A missing result breaks the sequence.
        for _, group in data.groupby(["INSTRUMENT_ID", "LOT_ID", "LEVEL_ID", "baseline_index"], sort=False):
            previous = np.nan
            for index, row in group.iterrows():
                if (row.z > 2 and previous > 2) or (row.z < -2 and previous < -2):
                    flags[index].append("2_2s")
                previous = row.z
        for index, row in data.iterrows():
            baseline = prepared[int(row.baseline_index)][0]
            rows.append(dict(source_result_column=result.name, run=row.RUN_ID, time=row.TIME_ID.isoformat(),
                level=row.LEVEL_ID, lot=row.LOT_ID, instrument=row.INSTRUMENT_ID, value=row.value,
                unit=unit, mean=baseline["MEAN"], sd=baseline["SD"], z=row.z,
                baseline_reference=baseline["REFERENCE"], baseline_valid_from=baseline["VALID_FROM"],
                baseline_valid_to=baseline["VALID_TO"], status="evaluated" if np.isfinite(row.z) else "not_evaluated",
                violation=", ".join(sorted(set(flags[index]))) or ("NONE" if np.isfinite(row.z) else "NE")))
    return pd.DataFrame(rows)
