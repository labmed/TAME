"""Actionable review of information that table values cannot define by themselves."""
from copy import deepcopy
import re

from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .toml_compat import dumps, loads

ALLOWED_DEFINITIONS = {"COLUMN", "CATEGORIES", "SETTINGS", "ANALYSIS_PLAN", "INIT_REVIEW", "INIT_OPTIONS"}


def load_definitions(path):
    if path is None:
        return {}
    value = loads(path.read_text(encoding="utf-8-sig"))
    unknown = set(value) - ALLOWED_DEFINITIONS
    if unknown:
        raise ValueError(f"Unknown init definition sections: {sorted(unknown)}")
    for section, config in value.items():
        if not isinstance(config, dict):
            raise ValueError(f"{section} must be a TOML table")
    settings = value.get("SETTINGS", {})
    if len({str(k).upper() for k in settings}) != len(settings):
        raise ValueError("Duplicate case-insensitive SETTINGS keys")
    if "SETTINGS" in value:
        value["SETTINGS"] = {str(k).upper(): v for k, v in settings.items()}
    if "DEFINE_" in dumps(value):
        raise ValueError("Replace DEFINE_ placeholders with reviewed definitions before applying this template")
    policy = ci_get(value.get("SETTINGS", {}), "CRR", None)
    if policy is not None and policy not in {"DELETE", "VALUE", "KEEP", "HARMONIZE"}:
        raise ValueError("SETTINGS.CRR must be DELETE, VALUE, KEEP or HARMONIZE")
    return value


def review_dataset(dataset, *, declared_settings=()):
    """Return observed counts, unresolved definitions and a fillable metadata template."""
    issues = []
    from .init_semantics import semantic_profiles
    profiles = {p["column"]: p for p in semantic_profiles(dataset)}
    source_columns = [c.name for c in dataset.columns if c.has_tag("SOURCE") or c.name.lower() in {"source", "source_dataset", "source_id", "dataset"}]
    sources = source_columns[0] if len(source_columns) == 1 else None
    multisource = sources is not None and dataset.df[sources].nunique() > 1
    template = {k: deepcopy(v) for k, v in dataset.meta.items() if k in ALLOWED_DEFINITIONS}
    template.setdefault("COLUMN", {})
    template.setdefault("CATEGORIES", {})
    template["SETTINGS"] = {k: v for k, v in template.get("SETTINGS", {}).items() if k in declared_settings}
    template.setdefault("INIT_REVIEW", {}).setdefault("BLANK_REASONS", {})
    def add(code, column, count, message, resolved=False, required=True, **extra):
        issues.append({"code": code, "column": column, "affected_cells": int(count),
                       "status": "RESOLVED" if resolved else "NEEDS_DEFINITION",
                       "required": required, "message": message, **extra})
    for column in dataset.columns:
        name = column.name;series = dataset.df[name]
        values = series.loc[series.map(lambda v: cell_state(v) == STATE_VALUE)]
        config = dataset.column_metadata(column)
        template["COLUMN"].setdefault(name, {})["TAGS"] = list(column.tags)
        if column.has_tag("RAW"):
            # Preserved source copies are evidence, not additional analysis variables.
            continue
        if column.has_tag("SEX"):
            profile = profiles[name]
            sex_source = profile["source_column"] or sources
            counts = {str(k): int(v) for k, v in values.value_counts().items()}
            numeric = {k: v for k, v in counts.items() if re.fullmatch(r"[+-]?\d+(?:\.0+)?", k.strip())}
            unresolved = profile["unrecognized_cells"]
            if numeric or unresolved:
                by_source = {}
                if sex_source:
                    for source, frame in dataset.df.groupby(sex_source, dropna=False, sort=False):
                        by_source[str(source)] = {str(k): int(v) for k, v in frame[name].value_counts().items()}
                add("SEX_CODE_MAP" if numeric else "SEX_VALUE_MAP", name, sum(numeric.values()) if numeric else unresolved,
                    "Define unrecognized sex values from each source codebook; identical codes can have different meanings.",
                    resolved=unresolved == 0, observed_codes=counts, source_column=sex_source or "", source_counts=by_source,
                    rows_scanned=len(series), unresolved_cells=unresolved)
                if unresolved:
                    key = "SEX_CODES_" + str(len(template["CATEGORIES"]) + 1)
                    template["COLUMN"][name]["TAGS"] = ["SEX", "CATEGORY", key]
                    entry = {"VALUES": ["male", "female", "other", "unknown"], "STRICT": True}
                    if sex_source:
                        entry["SOURCE_COLUMN"] = sex_source
                        entry["SOURCE_MAPS"] = {}
                        for item in profile["values"]:
                            if item["source"]:
                                entry["SOURCE_MAPS"].setdefault(item["source"], {})[item["raw_value"]] = item["canonical_value"] or "DEFINE_MEANING"
                        if not entry["SOURCE_MAPS"]:
                            entry["SOURCE_MAPS"] = {"DEFINE_SOURCE": {"DEFINE_CODE": "DEFINE_MEANING"}}
                    else:
                        entry["MAP"] = {item["raw_value"]: item["canonical_value"] or "DEFINE_MEANING" for item in profile["values"]}
                    template["CATEGORIES"][key] = entry
            if not len(values):
                add("SEX_NO_VALUES", name, len(series), "No observed sex values; the column name alone cannot validate SEX.")
        if column.has_tag("AGE"):
            profile = profiles[name]
            invalid = profile["unrecognized_cells"]
            if invalid or not len(values):
                add("AGE_VALUE_INVALID" if invalid else "AGE_NO_VALUES", name, invalid or len(series),
                    "Verify this is an individual age column and correct invalid values or override COLUMN.TAGS. Bare numbers default to years.",
                    rows_scanned=len(series), invalid_values={v["raw_value"]: v["count"] for v in profile["values"] if not v["recognized"]},
                    row_examples=profile["invalid_row_examples"])
            units = {key: int(value) for key, value in profile.get("units", {}).items() if value}
            if len(units) > 1:
                integer_config = ci_get(ci_get(dataset.meta, "INIT_OPTIONS", {}), "AGE_INTEGER", {}).get(name)
                add("AGE_MIXED_UNITS", name, sum(units.values()),
                    "Mixed age units were recognized. Confirm whether to add a completed-years integer column; the source AGE column remains unchanged.",
                    resolved=bool(integer_config), required=False, observed_units=units,
                    suggested_definition={"OUTPUT": "AGE_INTEGER", "METHOD": "FLOOR", "INVALID_POLICY": "ERROR"})
        if column.has_tag("<NUM>"):
            censored = values.astype(str).str.match(r"^[<>]=?").sum()
            if censored:
                policy = dataset.settings().get("CRR", "VALUE")
                declared = "CRR" in declared_settings
                add("COMPARATOR_POLICY", name, censored,
                    f"Starter CRR={policy}; confirm how inequalities enter a mean. VALUE uses boundaries, not observed concentrations.",
                    resolved=declared, effective_policy=policy)
                template["SETTINGS"]["CRR"] = policy
        if column.has_tag("RESULT"):
            if not config.get("ID"):
                add("ANALYTE_ID", name, len(values), "Define COLUMN.ID before aligning differently named assays.", required=False)
                template["COLUMN"][name]["ID"] = "DEFINE_ANALYTE_ID"
            if not config.get("UNIT"):
                add("RESULT_UNIT", name, len(values),
                    "Define the original unit before comparing or pooling sources; a numeric value does not establish unit compatibility.", required=multisource)
                template["COLUMN"][name]["UNIT"] = "DEFINE_SOURCE_UNIT"
        blanks = len(series) - len(values)
        if blanks:
            reason = ci_get(ci_get(dataset.meta, "INIT_REVIEW", {}), "BLANK_REASONS", {}).get(name, "")
            add("BLANK_REASON", name, blanks,
                "Blank cells have no per-cell clinical reason. Record UNKNOWN when no evidence exists; this does not relabel cells.",
                resolved=bool(reason), required=False)
            template["INIT_REVIEW"]["BLANK_REASONS"][name] = reason or "UNKNOWN"
    return issues, template


def template_text(template):
    return ("# Review observed issues in the accompanying .init-review.json.\n"
            "# This is a definition template, not a source of clinical facts.\n"
            "# Replace DEFINE_ placeholders or remove optional unresolved fields.\n"
            "# Numeric sex mappings must come from each source codebook.\n"
            "# COLUMN.UNIT declares the input unit; it does not convert values.\n"
            "# For mixed units in one column, separate sources before declaring units.\n"
            "# CRR options: DELETE, VALUE, KEEP, HARMONIZE; confirm the selected policy.\n"
            + dumps(template))
