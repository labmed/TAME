"""Executable observation, result-status and consumer-capability declarations."""
import numpy as np
import pandas as pd

from .analysis_contract import resolve_id, strict_numbers
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import ValidationIssue


CAPABILITIES = {
    "measurement_tags": 1, "observation": 1, "result_status": 1,
    "reference_interval": 1, "interval_censoring": 1, "analysis_models": 1,
    "integration_affine": 1, "effective_input_audit": 1,
}


def require_capabilities(dataset):
    config = ci_get(dataset.meta, "REQUIREMENTS", None)
    if config is None:
        return
    if not isinstance(config, dict) or set(config) != {"SEMANTICS_VERSION", "CAPABILITIES"}:
        raise ValueError("REQUIREMENTS needs SEMANTICS_VERSION and CAPABILITIES")
    if type(config["SEMANTICS_VERSION"]) is not int or config["SEMANTICS_VERSION"] != 1:
        raise ValueError("Unsupported TAME semantics version")
    required = config["CAPABILITIES"]
    if not isinstance(required, dict) or not required:
        raise ValueError("REQUIREMENTS.CAPABILITIES must be a nonempty version table")
    for name, version in required.items():
        if type(version) is not int or version != CAPABILITIES.get(name):
            raise ValueError(f"Unsupported required capability {name} version {version}")


def timestamp_series(series):
    values = []
    for value in series:
        if cell_state(value) != STATE_VALUE:
            values.append(pd.NaT)
            continue
        try:
            stamp = pd.Timestamp(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid observation timestamp") from exc
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError("Observation timestamps require an explicit timezone offset")
        values.append(stamp.tz_convert("UTC"))
    return pd.Series(pd.to_datetime(values, utc=True), index=series.index)


def key_frame(dataset, identifiers):
    if not isinstance(identifiers, list) or not identifiers or any(not isinstance(v, str) for v in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("Observation keys must be a nonempty list of distinct COLUMN.ID values")
    columns = [resolve_id(dataset, identifier).name for identifier in identifiers]
    frame = dataset.df[columns].copy()
    for name in columns:
        if frame[name].map(lambda v: cell_state(v) != STATE_VALUE or not str(v).strip()).any():
            raise ValueError("Missing observation/pairing key: " + name)
    return frame.astype(str)


def observation_info(dataset, *, inference=False, cluster_id=None):
    require_capabilities(dataset)
    config = ci_get(dataset.meta, "OBSERVATION", None)
    if config is None:
        return {"row_unit": "undeclared", "rows": len(dataset.df), "subjects": None, "repeated": False}
    required = {"VERSION", "ROW_UNIT", "KEY_IDS", "REPEAT_POLICY"}
    if not isinstance(config, dict) or not required <= set(config) or set(config) - required - {"SUBJECT_ID", "TIME_ID"}:
        raise ValueError("Invalid OBSERVATION declaration")
    if type(config["VERSION"]) is not int or config["VERSION"] != 1:
        raise ValueError("OBSERVATION.VERSION must be 1")
    if config["ROW_UNIT"] not in {"person", "specimen", "measurement", "run"}:
        raise ValueError("Unsupported OBSERVATION.ROW_UNIT")
    policy = config["REPEAT_POLICY"]
    if policy not in {"ERROR", "DESCRIPTIVE", "CLUSTER"}:
        raise ValueError("REPEAT_POLICY must be ERROR, DESCRIPTIVE or CLUSTER")
    if key_frame(dataset, config["KEY_IDS"]).duplicated().any():
        raise ValueError("Duplicate OBSERVATION.KEY_IDS; resolve repeats explicitly")
    subjects, repeated = None, False
    subject_id = config.get("SUBJECT_ID")
    if subject_id:
        subject = key_frame(dataset, [subject_id]).iloc[:, 0]
        subjects, repeated = subject.nunique(), subject.duplicated().any()
        if repeated and (policy == "ERROR" or config["ROW_UNIT"] == "person"):
            raise ValueError("Repeated subjects conflict with the observation declaration")
        if repeated and inference and (policy != "CLUSTER" or cluster_id != subject_id):
            raise ValueError("Repeated-subject inference requires CLUSTER with the declared SUBJECT_ID")
    elif policy == "CLUSTER":
        raise ValueError("CLUSTER requires OBSERVATION.SUBJECT_ID")
    if "TIME_ID" in config:
        times = timestamp_series(dataset.df[resolve_id(dataset, config["TIME_ID"]).name])
        if times.isna().any():
            raise ValueError("Declared observation times cannot be missing")
    return dict(row_unit=config["ROW_UNIT"], rows=len(dataset.df), subjects=subjects,
                repeated=bool(repeated), repeat_policy=policy, subject_id=subject_id)


def result_status(dataset, column):
    """Return eligibility and reasons; never infer a clinical HIL/dilution cutoff."""
    config = ci_get(dataset.column_metadata(column), "RESULT_CONTEXT", None)
    accepted, reasons = measurement_context(dataset, column)
    if config is None:
        return accepted, reasons
    required = {"STATUS_ID", "ACCEPTED_STATUSES"}
    optional = {"RAW_ID", "CHANGE_REFERENCE", "REASON_ID", "DILUTION_ID", "HIL_IDS", "RETEST_ID", "CANCEL_ID", "EXCLUDE_WHEN"}
    if not isinstance(config, dict) or not required <= set(config) or set(config) - required - optional:
        raise ValueError("RESULT_CONTEXT requires STATUS_ID and ACCEPTED_STATUSES")
    codes = config["ACCEPTED_STATUSES"]
    if not isinstance(codes, list) or not codes or any(not isinstance(v, str) or not v.strip() for v in codes) or len(set(codes)) != len(codes):
        raise ValueError("ACCEPTED_STATUSES requires distinct exact text codes")
    status = dataset.df[resolve_id(dataset, config["STATUS_ID"]).name]
    present = status.map(lambda v: cell_state(v) == STATE_VALUE)
    accepted &= present & status.isin(codes)
    reasons.loc[~present] = "STATUS_MISSING"
    reasons.loc[present & ~status.isin(codes)] = "STATUS_NOT_ACCEPTED"
    for key in ("RAW_ID", "REASON_ID", "DILUTION_ID", "RETEST_ID", "CANCEL_ID"):
        if key in config:
            bound = resolve_id(dataset, config[key])
            if bound.name == column.name:
                raise ValueError("Result context must bind a distinct source column")
            if key == "RAW_ID" and not str(config.get("CHANGE_REFERENCE", "")).strip():
                raise ValueError("RAW_ID requires a CHANGE_REFERENCE explaining the derivation/correction")
            if key == "DILUTION_ID":
                factors = strict_numbers(dataset.df[bound.name], "Dilution")
                if factors.dropna().le(0).any():
                    raise ValueError("Dilution factors must be positive; they are not automatically applied")
    if "HIL_IDS" in config:
        if not isinstance(config["HIL_IDS"], list) or not config["HIL_IDS"]:
            raise ValueError("HIL_IDS must list source columns")
        for identifier in config["HIL_IDS"]:
            resolve_id(dataset, identifier)
    if "EXCLUDE_WHEN" in config:
        from .planned_analysis import _conditions
        rule = config["EXCLUDE_WHEN"]
        if not isinstance(rule, dict) or set(rule) != {"ALL_OF"}:
            raise ValueError("EXCLUDE_WHEN requires ALL_OF")
        excluded, missing = _conditions(dataset, rule["ALL_OF"])
        accepted &= ~(excluded | missing)
        reasons.loc[excluded] = "EXCLUSION_RULE"
        reasons.loc[missing] = "EXCLUSION_CONTEXT_MISSING"
    return accepted, reasons


def measurement_context(dataset, column):
    config = ci_get(dataset.column_metadata(column), "MEASUREMENT_CONTEXT", None)
    accepted = pd.Series(True, index=dataset.df.index)
    reasons = pd.Series("", index=dataset.df.index, dtype=object)
    if config is None:
        return accepted, reasons
    bindings = {"METHOD_ID": "METHOD", "DEVICE_ID": "DEVICE", "CALIBRATION_ID": "CALIBRATION",
                "REAGENT_LOT_ID": "REAGENT_LOT", "CALIBRATOR_LOT_ID": "CALIBRATOR_LOT"}
    allowed = {*bindings, "TIME_ID", "VALID_FROM", "VALID_TO", "REFERENCE"}
    if not isinstance(config, dict) or set(config) - allowed or not isinstance(config.get("REFERENCE"), str) or not config["REFERENCE"].strip():
        raise ValueError("MEASUREMENT_CONTEXT needs reviewed bindings and REFERENCE")
    declaration = ci_get(dataset.column_metadata(column), "MEASUREMENT", {})
    for identifier, field in bindings.items():
        if identifier not in config:
            continue
        expected = ci_get(declaration, field, None)
        if not isinstance(expected, str) or not expected.strip():
            raise ValueError("MEASUREMENT_CONTEXT requires the accepted MEASUREMENT." + field)
        values = dataset.df[resolve_id(dataset, config[identifier]).name]
        matching = values.eq(expected)
        accepted &= matching
        reasons.loc[~matching] = "MEASUREMENT_CONTEXT_MISMATCH"
    validity = {"TIME_ID", "VALID_FROM", "VALID_TO"}
    if set(config) & validity:
        if not validity <= set(config):
            raise ValueError("Measurement validity requires TIME_ID, VALID_FROM and VALID_TO")
        start, end = timestamp_series(pd.Series([config["VALID_FROM"], config["VALID_TO"]]))
        if start >= end:
            raise ValueError("Measurement validity bounds must increase")
        times = timestamp_series(dataset.df[resolve_id(dataset, config["TIME_ID"]).name])
        matching = times.ge(start) & times.lt(end)
        accepted &= matching
        reasons.loc[~matching] = "MEASUREMENT_OUTSIDE_VALIDITY"
        reasons.loc[times.isna()] = "MEASUREMENT_TIME_MISSING"
    if not (set(config) & (set(bindings) | validity)):
        raise ValueError("MEASUREMENT_CONTEXT must bind identity or a validity period")
    return accepted, reasons


def observation_validation_issues(dataset):
    try:
        observation_info(dataset)
        for column in dataset.columns:
            result_status(dataset, column)
            config = ci_get(dataset.column_metadata(column), "REFERENCE_INTERVAL", None)
            if config is not None:
                from .reference_flags import reference_flags
                reference_flags(dataset, column)
        return []
    except (ValueError, TypeError, KeyError) as exc:
        return [ValidationIssue(0, "META", "OBSERVATION", None, str(exc))]
