"""Explicit, provenance-preserving integration of declared compatible assays."""
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import re

import pandas as pd

from .analysis_contract import column_ids, check_censoring_values, resolve_id
from .cellstate import STATE_VALUE, cell_state, parse_serialized_cell, serialize_cell
from .config import ci_get
from .models import ColumnSpec, OperationOutput, TameDataset, ValidationIssue
from .measurement_tags import require_measurement_tags, require_quantitative
from .units import convert_cell, decimal_number, decimal_text, unit_factor, unit_property


CONTEXT = {"COMPONENT", "SPECIMEN", "METHOD", "TIME", "SCALE", "PROPERTY"}
OPTIONAL_CONTEXT = {"DEVICE", "CALIBRATION", "REAGENT_LOT", "CALIBRATOR_LOT"}
PREFIX = ["source_id", "site_id", "source_row", "source_record_id", "source_row_json"]
SUFFIXES = ["", "__raw", "__raw_unit", "__llod", "__uloq", "__ref_low", "__ref_high",
            "__status", "__status_reason", "__censor_kind", "__censor_lower", "__censor_upper", "__released"]
LEGACY_SUFFIXES = SUFFIXES[:7]


def _object(value, required, optional=(), *, label):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError(f"{label}: required keys {sorted(required)}; allowed optional keys {sorted(optional)}")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: a nonempty string is required")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _cells(dataset):
    return [[str(serialize_cell(value)) for value in row] for row in dataset.df.itertuples(index=False, name=None)]


def _context(config, unit, label):
    value = _object(config, CONTEXT, OPTIONAL_CONTEXT, label=label)
    for key, item in value.items():
        _text(item, label + "." + key)
    if value["SCALE"] != "Qn" or value["PROPERTY"] != unit_property(unit):
        raise ValueError(f"PROPERTY_CONFLICT {label}: SCALE must be Qn and PROPERTY must match the unit")
    return value


def _profile(profile):
    profile = _object(profile, {"VERSION", "ID", "REFERENCE", "TARGETS", "SOURCES"}, {"BRIDGES", "UNIT_ALIASES"}, label="INTEGRATION_PROFILE")
    if type(profile["VERSION"]) is not int or profile["VERSION"] != 1:
        raise ValueError("INTEGRATION_PROFILE.VERSION must be 1")
    for key in ("ID", "REFERENCE"):
        _text(profile[key], key)
    if not isinstance(profile["TARGETS"], dict) or not profile["TARGETS"]:
        raise ValueError("TARGETS must be a nonempty table")
    for identifier, target in profile["TARGETS"].items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", identifier):
            raise ValueError("TARGET_ID must be a simple identifier of at most 64 characters")
        _object(target, {"UNIT", "MEASUREMENT"}, {"TAGS"}, label="TARGETS." + identifier)
        _context(target["MEASUREMENT"], target["UNIT"], identifier)
        from .tags import TAG_TOKEN_RE, parameterized_tag_parts, normalize_tag
        tags = target.get("TAGS", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not TAG_TOKEN_RE.fullmatch(tag) for tag in tags) or len(set(map(normalize_tag, tags))) != len(tags):
            raise ValueError("Target TAGS must be a list of distinct valid annotations")
        for tag in tags:
            parts = parameterized_tag_parts(tag)
            if not parts or parts[0] not in {"ANALYTE", "SPECIMEN", "PANEL"}:
                raise ValueError("Reviewed target TAGS supports ANALYTE, SPECIMEN and PANEL annotations")
            field = {"ANALYTE": "COMPONENT", "SPECIMEN": "SPECIMEN"}.get(parts[0])
            if field and normalize_tag(tag) != normalize_tag(parts[0] + "(" + target["MEASUREMENT"][field] + ")"):
                raise ValueError("Target annotation conflicts with MEASUREMENT")
    aliases = profile.get("UNIT_ALIASES", {})
    if not isinstance(aliases, dict):
        raise ValueError("UNIT_ALIASES must be a reviewed source-spelling to canonical-code table")
    for alias, canonical in aliases.items():
        _text(alias, "unit alias")
        unit_property(canonical)
        from .units import UNITS
        if alias in UNITS and alias != canonical:
            raise ValueError("UNIT_ALIASES cannot redefine a supported unit")
    names = PREFIX + [identifier + suffix for identifier in profile["TARGETS"] for suffix in SUFFIXES]
    if len(names) != len(set(names)):
        raise ValueError("TARGETS would create duplicate output column names")
    bridges = profile.get("BRIDGES", {})
    if not isinstance(bridges, dict):
        raise ValueError("BRIDGES must be a table")
    for identifier, bridge in bridges.items():
        _object(bridge, {"COMPONENT", "FROM_UNIT", "TO_UNIT", "FACTOR", "REFERENCE"},
                {"OFFSET", "ION_CHARGE", "FROM_CALIBRATION", "TO_CALIBRATION"}, label="BRIDGES." + identifier)
        _text(bridge["COMPONENT"], "bridge COMPONENT")
        _text(bridge["REFERENCE"], "bridge REFERENCE")
        properties = {unit_property(bridge["FROM_UNIT"]), unit_property(bridge["TO_UNIT"])}
        if properties not in [{"mass_concentration", "substance_concentration"},
                               {"equivalent_concentration", "substance_concentration"},
                               {"reported_percent", "substance_ratio"}]:
            raise ValueError("BRIDGE_DIMENSION_CONFLICT: unsupported directed property bridge")
        if decimal_number(bridge["FACTOR"], label="bridge FACTOR") <= 0:
            raise ValueError("Bridge FACTOR must be positive")
        offset = decimal_number(bridge.get("OFFSET", 0))
        if "equivalent_concentration" in properties:
            charge = bridge.get("ION_CHARGE")
            if type(charge) is not int or charge == 0 or abs(charge) > 10 or offset != 0:
                raise ValueError("Equivalent/mole bridges require a nonzero integer ION_CHARGE and zero offset")
            from decimal import Decimal
            if unit_property(bridge["FROM_UNIT"]) == "equivalent_concentration":
                expected = unit_factor(bridge["FROM_UNIT"], "mEq/L") * unit_factor("mmol/L", bridge["TO_UNIT"]) / Decimal(abs(charge))
            else:
                expected = unit_factor(bridge["FROM_UNIT"], "mmol/L") * Decimal(abs(charge)) * unit_factor("mEq/L", bridge["TO_UNIT"])
            if abs(decimal_number(bridge["FACTOR"]) - expected) > abs(expected) * Decimal("1e-12"):
                raise ValueError("Bridge FACTOR conflicts with ION_CHARGE and directed units")
        elif "ION_CHARGE" in bridge:
            raise ValueError("ION_CHARGE applies only to equivalent/mole bridges")
        if ("FROM_CALIBRATION" in bridge) != ("TO_CALIBRATION" in bridge):
            raise ValueError("Declare both calibration endpoints")
    if not isinstance(profile["SOURCES"], dict) or not profile["SOURCES"]:
        raise ValueError("SOURCES must be a nonempty table")
    for source, binding in profile["SOURCES"].items():
        _text(source, "source ID")
        _object(binding, {"MAPPINGS"}, {"IGNORE_IDS"}, label="SOURCES." + source)
        if not isinstance(binding["MAPPINGS"], list) or not binding["MAPPINGS"]:
            raise ValueError("Each source needs a nonempty MAPPINGS array")
        local, targets = set(), set()
        for mapping in binding["MAPPINGS"]:
            _object(mapping, {"COLUMN_ID", "TARGET_ID"}, {"BRIDGE"}, label="MAPPINGS")
            cid, tid = mapping["COLUMN_ID"], mapping["TARGET_ID"]
            _text(cid, "COLUMN_ID")
            _text(tid, "TARGET_ID")
            if cid in local or tid in targets:
                raise ValueError("Duplicate source or target mapping within a source")
            if tid not in profile["TARGETS"] or ("BRIDGE" in mapping and mapping["BRIDGE"] not in bridges):
                raise ValueError("Unknown TARGET_ID or BRIDGE")
            local.add(cid)
            targets.add(tid)
        ignored = binding.get("IGNORE_IDS", [])
        if not isinstance(ignored, list) or any(not isinstance(v, str) for v in ignored) or len(set(ignored)) != len(ignored) or local & set(ignored):
            raise ValueError("IGNORE_IDS must be a distinct list disjoint from mapped IDs")
    _json(profile)
    return profile


def _factor(column_config, target, mapping, profile):
    source_unit = ci_get(column_config, "UNIT", None)
    source_unit = profile.get("UNIT_ALIASES", {}).get(source_unit, source_unit)
    source_context = _context(ci_get(column_config, "MEASUREMENT", None), source_unit, mapping["COLUMN_ID"])
    bridge_id = mapping.get("BRIDGE")
    bridge = profile.get("BRIDGES", {}).get(bridge_id, {})
    for key in (CONTEXT - {"PROPERTY"}) | OPTIONAL_CONTEXT:
        if key == "CALIBRATION" and "FROM_CALIBRATION" in bridge:
            if (source_context.get(key), target["MEASUREMENT"].get(key)) != (bridge["FROM_CALIBRATION"], bridge["TO_CALIBRATION"]):
                raise ValueError("CALIBRATION_CONFLICT with reviewed bridge endpoints")
            continue
        if source_context.get(key) != target["MEASUREMENT"].get(key):
            raise ValueError(f"{key}_CONFLICT {mapping['COLUMN_ID']}: unit conversion does not establish assay equivalence")
    if bridge_id is None:
        return unit_factor(source_unit, target["UNIT"]), "Pint"
    bridge = profile["BRIDGES"][bridge_id]
    if (bridge["COMPONENT"], bridge["FROM_UNIT"], bridge["TO_UNIT"]) != (source_context["COMPONENT"], source_unit, target["UNIT"]):
        raise ValueError("BRIDGE_CONTEXT_CONFLICT: component and directed units must match exactly")
    return decimal_number(bridge["FACTOR"]), bridge["REFERENCE"]


def _bound(dataset, config, field, *, positive=False, expected_unit=None):
    direct, reference = ci_get(config, field, None), ci_get(config, field + "_ID", None)
    if direct is not None and reference is not None:
        raise ValueError(f"{field}: declare either a constant or a column ID, not both")
    if reference is not None:
        column = resolve_id(dataset, reference)
        require_quantitative(dataset, column, require_unit=True)
        if not dataset.column_has_tag(column, "NUM") or ci_get(dataset.column_metadata(column), "UNIT", None) != expected_unit:
            raise ValueError(f"{field}_UNIT_CONFLICT: bound columns must be NUM with the source result unit")
        values = dataset.df[column.name].tolist()
    else:
        values = [direct] * len(dataset.df)
    for i, value in enumerate(values, 1):
        if cell_state(value) == STATE_VALUE:
            number = decimal_number(value, label=f"{field} row {i}")
            if positive and number <= 0:
                raise ValueError(f"{field} must be positive")
    return values


def integrate_datasets(datasets, profile=None):
    """Return canonical wide results, lossless source-cell text, and a conversion audit."""
    datasets = list(datasets)
    if not datasets:
        raise ValueError("At least one source is required")
    if profile is None:
        profile = ci_get(datasets[0].meta, "INTEGRATION_PROFILE", None)
    profile = _profile(deepcopy(profile))
    frames, catalogue, audit = [], {}, []
    for dataset in datasets:
        require_measurement_tags(dataset)
        from .observation_contract import observation_info
        observation_info(dataset)
        if ci_get(dataset.meta, "SURVEY", None) is not None:
            raise ValueError("SURVEY integration is not supported; do not discard or pool survey designs")
        if ci_get(dataset.meta, "INTEGRATION_RESULT", None) is not None:
            raise ValueError("Already integrated: restore original sources before applying another profile")
        embedded = ci_get(dataset.meta, "INTEGRATION_PROFILE", None)
        if embedded is not None and _hash(embedded) != _hash(profile):
            raise ValueError("PROFILE_CONFLICT between the input declaration and requested profile")
        if list(dataset.df.columns) != [c.name for c in dataset.columns] or len(set(dataset.df.columns)) != len(dataset.columns):
            raise ValueError("Source columns must be distinct and agree with column specifications")
        identifiers = column_ids(dataset)
        source = ci_get(dataset.meta, "SOURCE", None)
        if not isinstance(source, dict):
            raise ValueError("SOURCE.ID, SITE_ID and RECORD_ID are required")
        sid, site, rid = [_text(ci_get(source, key, None), "SOURCE." + key) for key in ("ID", "SITE_ID", "RECORD_ID")]
        if sid in catalogue or sid not in profile["SOURCES"]:
            raise ValueError("Duplicate or unmapped SOURCE.ID")
        record_column = resolve_id(dataset, rid)
        binding = profile["SOURCES"][sid]
        mapped = {m["COLUMN_ID"] for m in binding["MAPPINGS"]}
        ignored = set(binding.get("IGNORE_IDS", []))
        if not (mapped | ignored) <= identifiers.keys():
            raise ValueError("Unknown mapped or ignored COLUMN.ID")
        for column in dataset.columns:
            if dataset.column_has_tag(column, "RESULT") and ci_get(dataset.column_metadata(column), "ID", None) not in mapped | ignored:
                raise ValueError(f"UNMAPPED_RESULT {column.name}: map it or explicitly list its ID in IGNORE_IDS")
        cells = _cells(dataset)
        catalogue[sid] = {"SITE_ID": site, "ROW_COUNT": len(cells), "CELL_SHA256": _hash(cells),
                          "COLUMNS": [{"NAME": c.name, "HEADER": c.original_header, "TAGS": list(c.tags)} for c in dataset.columns],
                          "META": deepcopy(dataset.meta), "SCHEMA": deepcopy(dataset.schema), "JOB": deepcopy(dataset.job)}
        catalogue[sid]["CONTROL_SHA256"] = _hash({key: catalogue[sid][key] for key in ("COLUMNS", "META", "SCHEMA", "JOB")})
        frame = pd.DataFrame({"source_id": [sid] * len(cells), "site_id": [site] * len(cells),
                              "source_row": [str(i) for i in range(1, len(cells) + 1)],
                              "source_record_id": dataset.df[record_column.name].tolist(),
                              "source_row_json": [_json(row) for row in cells]})
        for tid in profile["TARGETS"]:
            for suffix in SUFFIXES:
                frame[tid + suffix] = pd.Series([None] * len(cells), dtype=object)
        for mapping in binding["MAPPINGS"]:
            column = resolve_id(dataset, mapping["COLUMN_ID"])
            require_quantitative(dataset, column, require_unit=True)
            if dataset.column_has_tag(column, "AGE"):
                raise ValueError("Mapped measurements must declare NUM or <NUM>, not age or categorical values")
            config = dataset.column_metadata(column)
            tid = mapping["TARGET_ID"]
            factor, reference = _factor(config, profile["TARGETS"][tid], mapping, profile)
            offset = decimal_number(profile.get("BRIDGES", {}).get(mapping.get("BRIDGE"), {}).get("OFFSET", 0))
            source_unit = ci_get(config, "UNIT")
            bounds_config = deepcopy(config)
            censoring = ci_get(config, "CENSORING", None)
            if censoring is not None:
                check_censoring_values(dataset, column)
                limit = ci_get(censoring, "LIMIT", None)
                declared = _bound(dataset, config, "LLOD", positive=True, expected_unit=source_unit)
                has_llod = ci_get(config, "LLOD_ID", None) is not None or ci_get(config, "LLOD", None) is not None
                if has_llod and any(cell_state(v) != STATE_VALUE or decimal_number(v) != decimal_number(limit) for v in declared):
                    raise ValueError("LLOD_CONFLICT with the declared censoring binding")
                if ci_get(config, "LLOD_ID", None) is None:
                    bounds_config["LLOD"] = limit
            llod = _bound(dataset, bounds_config, "LLOD", positive=True, expected_unit=source_unit)
            uloq = _bound(dataset, config, "ULOQ", positive=True, expected_unit=source_unit)
            interval = ci_get(config, "REFERENCE_INTERVAL", {})
            _object(interval, set(), {"LOW", "HIGH", "LOW_ID", "HIGH_ID", "UNIT", "POPULATION", "REFERENCE", "APPLIES_WHEN", "TIME_ID", "VALID_FROM", "VALID_TO"}, label="REFERENCE_INTERVAL")
            low = _bound(dataset, interval, "LOW", expected_unit=source_unit)
            high = _bound(dataset, interval, "HIGH", expected_unit=source_unit)
            if interval:
                from .reference_flags import reference_flags
                _, reasons, _ = reference_flags(dataset, column)
                for position, reason in enumerate(reasons):
                    if reason in {"REFERENCE_CONTEXT_MISSING", "REFERENCE_NOT_APPLICABLE"}:
                        low[position], high[position] = None, None
            for i, (a, b) in enumerate(zip(low, high), 1):
                if cell_state(a) == cell_state(b) == STATE_VALUE and decimal_number(a) > decimal_number(b):
                    raise ValueError(f"REFERENCE_INTERVAL row {i}: lower bound exceeds upper bound")
            for i, (a, b) in enumerate(zip(llod, uloq), 1):
                if cell_state(a) == cell_state(b) == STATE_VALUE and decimal_number(a) > decimal_number(b):
                    raise ValueError(f"LLOD exceeds ULOQ at row {i}")
            raw = dataset.df[column.name].tolist()
            frame[tid + "__raw"] = pd.Series(raw, dtype=object)
            frame[tid + "__raw_unit"] = source_unit
            from .observation_contract import result_status
            accepted, reasons = result_status(dataset, column)
            frame[tid + "__status"] = ["ACCEPTED" if v else "EXCLUDED" for v in accepted]
            frame[tid + "__status_reason"] = reasons.to_numpy()
            interval_binding = ci_get(config, "CENSORING_INTERVAL", None)
            if interval_binding is not None:
                from .interval_censoring import intervals
                released, kinds, lower, upper = intervals(dataset, column)
                frame[tid + "__censor_kind"] = kinds.to_numpy()
                for suffix, values in [("__censor_lower", lower), ("__censor_upper", upper), ("__released", released)]:
                    frame[tid + suffix] = [convert_cell(v, factor, offset=offset) for v in values]
            else:
                from .analysis import parse_comparator_number
                parsed = [parse_comparator_number(str(v).strip()) if cell_state(v) == STATE_VALUE else None for v in raw]
                kinds = {"<": "LEFT", "<=": "LEFT_CLOSED", ">": "RIGHT", ">=": "RIGHT_CLOSED", "": "EXACT", "=": "EXACT"}
                frame[tid + "__censor_kind"] = [kinds[p[0]] if p else None for p in parsed]
                for suffix, operators in [("__censor_lower", {">", ">="}), ("__censor_upper", {"<", "<="})]:
                    frame[tid + suffix] = [convert_cell(p[1], factor, offset=offset) if p and p[0] in operators else None for p in parsed]
                if censoring is not None:
                    released = dataset.df[resolve_id(dataset, ci_get(censoring, "SOURCE_ID")).name]
                else:
                    released = [p[1] if p and p[0] in {"", "="} else None for p in parsed]
                frame[tid + "__released"] = [convert_cell(v, factor, offset=offset) for v in released]
            for suffix, values in [("", raw), ("__llod", llod), ("__uloq", uloq), ("__ref_low", low), ("__ref_high", high)]:
                frame[tid + suffix] = pd.Series([convert_cell(v, factor, offset=offset, comparators=(suffix == "" and dataset.column_has_tag(column, "<NUM>")),
                                                              label=f"{sid}/{column.name}/{i}") for i, v in enumerate(values, 1)], dtype=object)
            audit.append({"source_id": sid, "site_id": site, "column_id": mapping["COLUMN_ID"], "target_id": tid,
                          "source_unit": ci_get(config, "UNIT"), "target_unit": profile["TARGETS"][tid]["UNIT"],
                          "factor": decimal_text(factor), "offset": decimal_text(offset), "reference": reference, "rows": len(raw),
                          "nonvalue_states": sum(cell_state(v) != STATE_VALUE for v in raw)})
        frames.append(frame)
    states = ["NULLABLE", "EMPTY_OK", "WS_OK"]
    columns = [ColumnSpec(name, name, ["BY", "STR"] if name in {"source_id", "site_id"} else ["STR", *states]) for name in PREFIX]
    metadata = {name: {"ID": name} for name in PREFIX}
    for tid, target in profile["TARGETS"].items():
        interval_target = any(ci_get(ds.column_metadata(resolve_id(ds, m["COLUMN_ID"])), "CENSORING_INTERVAL", None) is not None
            for ds in datasets for m in profile["SOURCES"][ci_get(ci_get(ds.meta, "SOURCE"), "ID")]["MAPPINGS"] if m["TARGET_ID"] == tid)
        for suffix in SUFFIXES:
            name = tid + suffix
            text_suffixes = {"__raw", "__raw_unit", "__status", "__status_reason", "__censor_kind"}
            tags = ["RESULT", "<NUM>", *states, *target.get("TAGS", [])] if not suffix else (["STR", "ORIGINAL", *states] if suffix in text_suffixes else ["<NUM>" if suffix == "__released" else "NUM", *states])
            columns.append(ColumnSpec(name, name, tags))
            metadata[name] = {"ID": name}
            if suffix not in text_suffixes:
                metadata[name]["UNIT"] = target["UNIT"]
            if not suffix:
                metadata[name]["MEASUREMENT"] = deepcopy(target["MEASUREMENT"])
                metadata[name]["RESULT_CONTEXT"] = {"STATUS_ID": tid + "__status", "ACCEPTED_STATUSES": ["ACCEPTED"],
                    "RAW_ID": tid + "__raw", "REASON_ID": tid + "__status_reason", "CHANGE_REFERENCE": profile["REFERENCE"]}
                metadata[name]["REFERENCE_INTERVAL"] = {"LOW_ID": tid + "__ref_low", "HIGH_ID": tid + "__ref_high", "UNIT": target["UNIT"], "REFERENCE": profile["REFERENCE"]}
                if interval_target:
                    metadata[name]["CENSORING_INTERVAL"] = {"VERSION": 1, "RELEASED_ID": tid + "__released", "KIND_ID": tid + "__censor_kind",
                        "LOWER_ID": tid + "__censor_lower", "UPPER_ID": tid + "__censor_upper", "REFERENCE": profile["REFERENCE"]}
                metadata[name]["INTEGRATION"] = {"RAW_ID": tid + "__raw", "RAW_UNIT_ID": tid + "__raw_unit", "LLOD_ID": tid + "__llod", "ULOQ_ID": tid + "__uloq",
                                                 "REFERENCE_LOW_ID": tid + "__ref_low", "REFERENCE_HIGH_ID": tid + "__ref_high"}
    # Fresh metadata avoids inheriting one source's analysis policy or survey design.
    meta = {"ANALYSIS_CONTRACT": {"VERSION": 1}, "COLUMN": metadata, "SETTINGS": {"CRR": "DELETE"},
            "WORKS": {"DEFAULT": ["VALIDATE", "DESCRIBE"]},
            "REQUIREMENTS": {"SEMANTICS_VERSION": 1, "CAPABILITIES": {"integration_affine": 1, "measurement_tags": 1, "result_status": 1, "reference_interval": 1}},
            "OBSERVATION": {"VERSION": 1, "ROW_UNIT": "measurement", "KEY_IDS": ["source_id", "source_row"], "REPEAT_POLICY": "DESCRIPTIVE"},
            "INTEGRATION_RESULT": {"VERSION": 2, "PROFILE": profile, "PROFILE_SHA256": _hash(profile),
                                   "EXPECTED_ROWS": sum(len(frame) for frame in frames), "KEY_FIELDS": ["source_id", "source_row"],
                                   "ENGINE": "Pint " + importlib.metadata.version("pint"),
                                   "SCOPE": "Declared compatibility and unit conversion; not patient linkage or analytical-method validation"},
            "SOURCE_CATALOG": catalogue}
    result = TameDataset(pd.concat(frames, ignore_index=True)[[c.name for c in columns]], columns, meta=meta)
    from .observation_contract import observation_validation_issues
    from .analysis_contract import contract_validation_issues
    issues = observation_validation_issues(result) + contract_validation_issues(result)
    if issues:
        raise ValueError("Integrated semantic contract: " + issues[0].message)
    return OperationOutput(name="INTEGRATE", dataset=result, table=pd.DataFrame(audit),
                           warnings=["Declared assay identity is not proof of commutability. Original identifiers are retained, not anonymized."],
                           message=f"sources={len(datasets)} rows={len(result.df)} targets={len(profile['TARGETS'])}")


def _canonicalized(dataset):
    identifiers = column_ids(dataset)
    if len(identifiers) != len(dataset.columns):
        raise ValueError("Every integrated column requires a unique COLUMN.ID")
    names = {column.name: identifier for identifier, column in identifiers.items()}
    metadata = deepcopy(dataset.meta)
    metadata["COLUMN"] = {names[column.name]: deepcopy(dataset.column_metadata(column)) for column in dataset.columns}
    return dataset.replace(df=dataset.df.rename(columns=names),
                           columns=[ColumnSpec(names[c.name], names[c.name], c.tags) for c in dataset.columns], meta=metadata)


def restore_integration_sources(dataset):
    """Restore original TAME cell text/states and controls, rejecting broken provenance."""
    dataset = _canonicalized(dataset)
    result = ci_get(dataset.meta, "INTEGRATION_RESULT", {})
    _object(result, {"VERSION", "PROFILE", "PROFILE_SHA256", "EXPECTED_ROWS", "KEY_FIELDS", "ENGINE", "SCOPE"}, label="INTEGRATION_RESULT")
    if type(result.get("VERSION")) is not int or result["VERSION"] not in {1, 2}:
        raise ValueError("INTEGRATION_RESULT.VERSION must be 1 or 2")
    if type(result["EXPECTED_ROWS"]) is not int or result["EXPECTED_ROWS"] < 0 or result["KEY_FIELDS"] != ["source_id", "source_row"]:
        raise ValueError("Invalid integration row count or KEY_FIELDS")
    catalogue = ci_get(dataset.meta, "SOURCE_CATALOG", None)
    if not isinstance(catalogue, dict) or not catalogue or not set(PREFIX) <= set(dataset.df.columns):
        raise ValueError("Missing integration source catalogue or provenance columns")
    if len(dataset.df) != result["EXPECTED_ROWS"] or dataset.df.duplicated(["source_id", "source_row"]).any():
        raise ValueError("Integration row count or provenance key conflict")
    if not set(dataset.df.source_id) <= catalogue.keys():
        raise ValueError("Unknown source_id in integrated rows")
    restored = {}
    for sid, source in catalogue.items():
        _object(source, {"SITE_ID", "ROW_COUNT", "CELL_SHA256", "COLUMNS", "META", "SCHEMA", "JOB", "CONTROL_SHA256"}, label="SOURCE_CATALOG")
        if type(source["ROW_COUNT"]) is not int or source["ROW_COUNT"] < 0:
            raise ValueError("Invalid source ROW_COUNT")
        if not all(isinstance(source[key], dict) for key in ("META", "SCHEMA", "JOB")) or not isinstance(source["COLUMNS"], list):
            raise ValueError("Malformed source controls")
        original_source = ci_get(source["META"], "SOURCE", {})
        if not isinstance(original_source, dict) or ci_get(original_source, "ID", None) != sid or ci_get(original_source, "SITE_ID", None) != source["SITE_ID"]:
            raise ValueError("Source catalogue identity conflicts with original source controls")
        for column in source["COLUMNS"]:
            _object(column, {"NAME", "HEADER", "TAGS"}, label="SOURCE_CATALOG.COLUMNS")
            if not isinstance(column["NAME"], str) or not isinstance(column["HEADER"], str) or not isinstance(column["TAGS"], list) or any(not isinstance(tag, str) for tag in column["TAGS"]):
                raise ValueError("Malformed original column specification")
        selected = dataset.df.loc[dataset.df.source_id.eq(sid)].copy()
        if any(not re.fullmatch(r"[1-9]\d*", str(v)) for v in selected.source_row):
            raise ValueError("source_row must be a positive row number")
        selected = selected.assign(_row=selected.source_row.astype(int)).sort_values("_row")
        if selected._row.tolist() != list(range(1, source["ROW_COUNT"] + 1)):
            raise ValueError("Missing, duplicate or reordered original row numbers")
        cells = [json.loads(value) for value in selected.source_row_json]
        if _hash(cells) != source["CELL_SHA256"]:
            raise ValueError("Original source-cell checksum mismatch")
        controls = {key: source[key] for key in ("COLUMNS", "META", "SCHEMA", "JOB")}
        if _hash(controls) != source["CONTROL_SHA256"]:
            raise ValueError("Original source-control checksum mismatch")
        columns = [ColumnSpec(c["HEADER"], c["NAME"], c["TAGS"]) for c in source["COLUMNS"]]
        if any(not isinstance(row, list) or len(row) != len(columns) or any(not isinstance(v, str) for v in row) for row in cells):
            raise ValueError("Malformed original source row")
        frame = pd.DataFrame([[parse_serialized_cell(v) for v in row] for row in cells], columns=[c.name for c in columns])
        restored[sid] = TameDataset(frame, columns, meta=deepcopy(source["META"]), schema=deepcopy(source["SCHEMA"]), job=deepcopy(source["JOB"]))
    return restored


def integration_validation_issues(dataset):
    if ci_get(dataset.meta, "INTEGRATION_RESULT", None) is None:
        return []
    try:
        dataset = _canonicalized(dataset)
        result = ci_get(dataset.meta, "INTEGRATION_RESULT")
        if not isinstance(result, dict):
            raise ValueError("INTEGRATION_RESULT must be a table")
        profile = result["PROFILE"]
        if _hash(profile) != result["PROFILE_SHA256"]:
            raise ValueError("Integration profile checksum mismatch")
        originals = restore_integration_sources(dataset)
        expected = integrate_datasets(originals.values(), profile).dataset
        if result["VERSION"] == 1:
            names = PREFIX + [identifier + suffix for identifier in profile["TARGETS"] for suffix in LEGACY_SUFFIXES]
            metadata = deepcopy(expected.meta)
            metadata["COLUMN"] = {name: {key: value for key, value in expected.meta["COLUMN"][name].items()
                if key in {"ID", "UNIT", "MEASUREMENT", "INTEGRATION"}} for name in names}
            expected = expected.replace(df=expected.df[names], columns=[c for c in expected.columns if c.name in names], meta=metadata)
        if {c.name: c.tags for c in dataset.columns} != {c.name: c.tags for c in expected.columns} or dataset.meta["COLUMN"] != expected.meta["COLUMN"]:
            raise ValueError("Integrated column identity, units or measurement context changed")
        keys = ["source_id", "source_row"]
        actual_rows = dataset.df[[c.name for c in expected.columns]].sort_values(keys).reset_index(drop=True)
        expected_rows = expected.df.sort_values(keys).reset_index(drop=True)
        if _cells(expected.with_df(actual_rows)) != _cells(expected.with_df(expected_rows)):
            raise ValueError("Integrated values, bounds or provenance contradict the original source and profile")
    except (ValueError, KeyError, TypeError, ImportError, OverflowError) as exc:
        return [ValidationIssue(0, "META", "INTEGRATION", None, str(exc))]
    return []
