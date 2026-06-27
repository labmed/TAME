from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import ci_get
from .tags import normalize_tag, parameterized_tag_parts


@dataclass(frozen=True)
class TagDefinition:
    name: str
    label: str
    description: str = ""
    children: tuple["TagDefinition", ...] = field(default_factory=tuple)


BUILTIN_TAG_PARENTS: dict[str, tuple[str, ...]] = {
    "CAT": ("CATEGORY",),
    "DOB": ("BIRTHDATE", "DATE"),
    "PATIENT_ID": ("ID(patient)", "ID"),
    "HOSPITAL_ID": ("ID(hospital)", "ID"),
    "FACILITY_ID": ("ID(facility)", "ID"),
    "ORGANIZATION_ID": ("ID(organization)", "ID"),
    "SAMPLE_ID": ("ID(sample)", "ID"),
    "SPECIMEN_ID": ("ID(specimen)", "ID"),
    "ORDER_ID": ("ID(order)", "ID"),
    "ENCOUNTER_ID": ("ID(encounter)", "ID"),
    "VISIT_ID": ("ID(visit)", "ID"),
    "COLLECTION_DATE": ("DATE",),
    "COLLECTION_AT": ("DATETIME",),
    "RECEIVED_DATE": ("DATE",),
    "REPORT_DATE": ("DATE",),
    "CALIBRATION_DATE": ("DATE",),
    "RESULT_TIME": ("DATETIME",),
    "RECEIVED_AT": ("DATETIME",),
    "ORDER_DATE": ("DATE",),
    "ORDER_AT": ("DATETIME",),
    "STAGE": ("CATEGORY",),
    "EVENT": ("CATEGORY",),
    "ORDER_PLACED_AT": ("DATETIME",),
    "SPECIMEN_COLLECTED_AT": ("DATETIME",),
    "LAB_RECEIVED_AT": ("DATETIME",),
    "LAB_SECTION_RECEIVED_AT": ("DATETIME",),
    "TEST_STARTED_AT": ("DATETIME",),
    "RESULT_CREATED_AT": ("DATETIME",),
    "PRELIMINARY_REPORTED_AT": ("DATETIME",),
    "FINAL_REPORTED_AT": ("DATETIME",),
}

PARAMETERIZED_TAG_PARENTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("ID", "patient"): ("PATIENT_ID",),
    ("ID", "hospital"): ("HOSPITAL_ID",),
    ("ID", "facility"): ("FACILITY_ID",),
    ("ID", "organization"): ("ORGANIZATION_ID",),
    ("ID", "sample"): ("SAMPLE_ID",),
    ("ID", "specimen"): ("SPECIMEN_ID",),
    ("ID", "order"): ("ORDER_ID",),
    ("ID", "encounter"): ("ENCOUNTER_ID",),
    ("ID", "visit"): ("VISIT_ID",),
}


TAG_CATALOG: tuple[TagDefinition, ...] = (
    TagDefinition(
        "IDENTITY",
        "Identity and provenance",
        "Columns that identify a patient, specimen, row, site, or source dataset.",
        (
            TagDefinition("ID", "Identifier", "Generic identifier; hash by default when anonymizing."),
            TagDefinition("ID(patient)", "Patient ID", "Preferred patient-level identifier tag."),
            TagDefinition("ID(hospital)", "Hospital ID", "Preferred institution-level identifier tag."),
            TagDefinition("ID(sample)", "Sample ID", "Preferred specimen/sample identifier tag."),
            TagDefinition("ID(order)", "Order ID", "Preferred order/request identifier tag."),
            TagDefinition("ID(specimen)", "Specimen ID", "Preferred specimen identifier tag."),
            TagDefinition("ID(encounter)", "Encounter ID", "Preferred encounter-level identifier tag."),
            TagDefinition("ID(visit)", "Visit ID", "Preferred visit-level identifier tag."),
            TagDefinition("ID(facility)", "Facility ID", "Preferred facility/site identifier tag."),
            TagDefinition("ID(organization)", "Organization ID", "Preferred organization identifier tag."),
            TagDefinition("PATIENT_ID", "Patient ID alias", "Legacy alias for ID(patient)."),
            TagDefinition("HOSPITAL_ID", "Hospital ID alias", "Legacy alias for ID(hospital)."),
            TagDefinition("SAMPLE_ID", "Sample ID alias", "Legacy alias for ID(sample)."),
            TagDefinition("ORDER_ID", "Order ID alias", "Legacy alias for ID(order)."),
            TagDefinition("SPECIMEN_ID", "Specimen ID alias", "Legacy alias for ID(specimen)."),
            TagDefinition("ENCOUNTER_ID", "Encounter ID alias", "Legacy alias for ID(encounter)."),
            TagDefinition("VISIT_ID", "Visit ID alias", "Legacy alias for ID(visit)."),
            TagDefinition("FACILITY_ID", "Facility ID alias", "Legacy alias for ID(facility)."),
            TagDefinition("ORGANIZATION_ID", "Organization ID alias", "Legacy alias for ID(organization)."),
            TagDefinition("ROWID", "Row ID", "Original row number or record sequence."),
            TagDefinition("SHEET", "Sheet/source", "Worksheet or source table label."),
            TagDefinition("SOURCE", "Source", "Dataset/source provenance column."),
            TagDefinition("NAME", "Name", "Direct personal name; drop by default when anonymizing."),
        ),
    ),
    TagDefinition(
        "MEASURE",
        "Measurements",
        "Numeric, result, unit, and reference interval fields.",
        (
            TagDefinition("RESULT", "Result", "Measured or calculated result value."),
            TagDefinition("NUM", "Strict numeric", "Numeric value without inequality/comparator signs."),
            TagDefinition("<NUM>", "Comparator numeric", "Numeric value that may include <, <=, >, >=, or =."),
            TagDefinition("UNIT", "Unit", "Measurement unit."),
            TagDefinition("SOURCE_UNIT", "Source unit", "Original measurement unit before conversion."),
            TagDefinition("TARGET_UNIT", "Target unit", "Target measurement unit after conversion."),
            TagDefinition("CONVERSION_FACTOR", "Conversion factor", "Numeric factor used for unit conversion."),
            TagDefinition("RAW_RESULT", "Raw result", "Original textual result before parsing or normalization."),
            TagDefinition("NORMALIZED_RESULT", "Normalized result", "Parsed or unit-normalized result value."),
            TagDefinition("PARSE_STATUS", "Parse status", "Parsing or normalization status such as CONVERTED or PARSE_ERROR."),
            TagDefinition("COUNT", "Count", "Count statistic."),
            TagDefinition("N", "N", "Number of observations."),
            TagDefinition("MEAN", "Mean", "Arithmetic mean statistic."),
            TagDefinition("MEDIAN", "Median", "Median statistic."),
            TagDefinition("SD", "Standard deviation", "Standard deviation statistic."),
            TagDefinition("MIN", "Minimum", "Minimum statistic."),
            TagDefinition("MAX", "Maximum", "Maximum statistic."),
            TagDefinition("PERCENT", "Percent", "Percentage or rate."),
            TagDefinition("PERCENTILE", "Percentile", "Percentile statistic or threshold."),
            TagDefinition("RANK", "Rank", "Rank/order statistic."),
            TagDefinition("RATIO", "Ratio", "Ratio or relative value."),
            TagDefinition("DURATION", "Duration", "Elapsed time or interval duration."),
            TagDefinition("THRESHOLD", "Threshold", "Decision or cutoff threshold."),
            TagDefinition("CORRELATION", "Correlation", "Correlation coefficient."),
            TagDefinition("SLOPE", "Slope", "Regression slope."),
            TagDefinition("INTERCEPT", "Intercept", "Regression intercept."),
            TagDefinition("P_VALUE", "P-value", "Statistical p-value."),
            TagDefinition("SIGMA", "Sigma", "Sigma metric."),
            TagDefinition("EFFECT_SIZE", "Effect size", "Effect size statistic."),
            TagDefinition("DEGREES_OF_FREEDOM", "Degrees of freedom", "Statistical degrees of freedom."),
            TagDefinition("AUC", "Area under curve", "ROC area under the curve."),
            TagDefinition("SENSITIVITY", "Sensitivity", "Diagnostic sensitivity."),
            TagDefinition("SPECIFICITY", "Specificity", "Diagnostic specificity."),
            TagDefinition("YOUDEN", "Youden index", "Youden J statistic."),
            TagDefinition("REF_LOW", "Reference low", "Reference interval lower limit."),
            TagDefinition("REF_HIGH", "Reference high", "Reference interval upper limit."),
            TagDefinition("REF_WIDTH", "Reference width", "Reference interval width."),
            TagDefinition("UNIT_RESULT", "Unit result", "Original textual result including unit."),
            TagDefinition("DILUTION", "Dilution", "Dilution factor."),
            TagDefinition("EXPECTED", "Expected", "Expected/reference-method result."),
            TagDefinition("BIAS", "Bias", "Difference or bias value."),
            TagDefinition("SPIKE", "Spike", "Spiked concentration."),
            TagDefinition("RECOVERY", "Recovery", "Recovery percentage."),
        ),
    ),
    TagDefinition(
        "GROUPING",
        "Grouping and categories",
        "Columns used for category distributions, stratification, and BY-group summaries.",
        (
            TagDefinition("CATEGORY", "Category", "Categorical column; EDA reports counts and percentages per category."),
            TagDefinition("CAT", "Category alias", "Short alias that inherits CATEGORY behavior."),
            TagDefinition("STAGE", "Workflow stage", "Workflow or process stage label."),
            TagDefinition("EVENT", "Workflow event", "Workflow event or milestone label."),
            TagDefinition("BY", "By group", "Grouping column; RESULT summaries are calculated separately per BY value."),
            TagDefinition("GROUP", "Group", "Generic cohort/group variable."),
            TagDefinition("COHORT", "Cohort", "Study cohort or analysis population."),
            TagDefinition("POPULATION", "Population", "Analysis population label."),
            TagDefinition("LABEL", "Label", "Outcome or class label for supervised analysis."),
            TagDefinition("OUTCOME", "Outcome", "Clinical or diagnostic outcome label."),
            TagDefinition("CLASS", "Class", "Class label for classification tasks."),
            TagDefinition("PATIENT_TYPE", "Patient type", "Outpatient/inpatient/emergency or similar encounter type."),
            TagDefinition("WARD", "Ward/department", "Ward, clinic, or ordering department."),
            TagDefinition("INSURANCE_TYPE", "Insurance type", "Insurance or payer category for stratified analysis."),
            TagDefinition("RESIDENCE_REGION", "Residence region", "Patient residence region or administrative area."),
            TagDefinition("TESTNAME", "Test name", "Laboratory test or measurement name."),
            TagDefinition("ITEM", "Item", "Item/analyte label."),
            TagDefinition("SEX", "Sex", "Canonical values: male, female, other, unknown."),
            TagDefinition("AGE", "Age", "Age value. Bare numbers are years; UCUM units a, mo, d are supported."),
            TagDefinition("AGE(baseline)", "Baseline age", "Baseline or registration age; inherits AGE behavior."),
            TagDefinition("AGE(visit)", "Visit age", "Age calculated at specimen collection, visit, or encounter time; inherits AGE behavior."),
            TagDefinition("DOB", "Date of birth", "Birth date alias that inherits DATE behavior."),
            TagDefinition("AGE_GROUP", "Age group", "Derived age band."),
            TagDefinition("SEX_GROUP", "Sex group", "Derived sex stratum."),
            TagDefinition("INSTRUMENT", "Instrument", "Analyzer/instrument category."),
            TagDefinition("DEVICE", "Device", "Device category."),
            TagDefinition("EQUIPMENT", "Equipment", "Equipment category."),
            TagDefinition("SAMPLE_TYPE", "Sample type", "Specimen type such as serum, plasma, whole blood."),
            TagDefinition("COLLECTION_SITE", "Collection site", "Specimen collection body/site label."),
        ),
    ),
    TagDefinition(
        "TIME",
        "Date and time",
        "Temporal fields for parsing, trend, workload, and turnaround-time analysis.",
        (
            TagDefinition("DATETIME", "Datetime", "Datetime-like value; EDA reports parseability."),
            TagDefinition("DATE", "Date", "Date value; DATE(format) uses Python strftime/strptime format."),
            TagDefinition("TIME", "Time", "Time value."),
            TagDefinition("HOUR", "Hour", "Hour-of-day value."),
            TagDefinition("BIRTHDATE", "Birth date", "Date of birth."),
            TagDefinition("COLLECTION_DATE", "Collection date", "Specimen collection date."),
            TagDefinition("COLLECTION_AT", "Collection datetime", "Specimen collection timestamp."),
            TagDefinition("RECEIVED_DATE", "Received date", "Specimen/order received date."),
            TagDefinition("RECEIVED_AT", "Received time", "Specimen/order received timestamp."),
            TagDefinition("REPORT_DATE", "Report date", "Report release date."),
            TagDefinition("RESULT_TIME", "Result time", "Result/test completed timestamp."),
            TagDefinition("CALIBRATION_DATE", "Calibration date", "Analyzer or reagent calibration date."),
            TagDefinition("ORDER_PLACED_AT", "Order placed time", "Clinical order entry timestamp."),
            TagDefinition("SPECIMEN_COLLECTED_AT", "Specimen collected time", "Specimen collection/phlebotomy timestamp."),
            TagDefinition("LAB_RECEIVED_AT", "Laboratory received time", "Timestamp when the central laboratory received the specimen."),
            TagDefinition("LAB_SECTION_RECEIVED_AT", "Laboratory section received time", "Timestamp when the specimen arrived at the responsible laboratory section or bench."),
            TagDefinition("TEST_STARTED_AT", "Test started time", "Timestamp when analytical testing started."),
            TagDefinition("RESULT_CREATED_AT", "Result created time", "Timestamp when an instrument/LIS result was generated."),
            TagDefinition("PRELIMINARY_REPORTED_AT", "Preliminary report time", "Timestamp when an interim/preliminary report was released."),
            TagDefinition("FINAL_REPORTED_AT", "Final report time", "Timestamp when the final report was released."),
        ),
    ),
    TagDefinition(
        "LAB",
        "Laboratory metadata",
        "Clinical laboratory method, code, interpretation, and quality-control fields.",
        (
            TagDefinition("METHOD", "Method", "Analytical method."),
            TagDefinition("LOINC", "LOINC", "LOINC code."),
            TagDefinition("LOCAL_CODE", "Local code", "Local laboratory test code."),
            TagDefinition("REAGENT_LOT", "Reagent lot", "Reagent lot number."),
            TagDefinition("INTERPRETATION", "Interpretation", "Result interpretation flag such as H, L, N, A, C."),
            TagDefinition("CRITICAL", "Critical", "Critical-value flag."),
            TagDefinition("CRITICAL_FLAG", "Critical flag", "Critical-value rule result."),
            TagDefinition("DELTA", "Delta", "Delta-check flag or change value."),
            TagDefinition("QC_LEVEL", "QC level", "Quality-control level."),
            TagDefinition("QC_RULE", "QC rule", "Quality-control rule identifier."),
            TagDefinition("WESTGARD_FLAG", "Westgard flag", "Westgard rule result."),
            TagDefinition("QC_TARGET", "QC target", "Quality-control assigned target value."),
            TagDefinition("QC_SD", "QC SD", "Quality-control standard deviation."),
            TagDefinition("QC_STATUS", "QC status", "Quality-control pass/fail status."),
            TagDefinition("RULESET", "Ruleset", "Rule collection identifier."),
            TagDefinition("RULE_ID", "Rule ID", "Specific rule identifier."),
            TagDefinition("DECISION", "Decision", "Rule-engine decision such as PASS, REVIEW, or HOLD."),
            TagDefinition("REVIEW_REASON", "Review reason", "Reason a result needs review or hold."),
            TagDefinition("AUTOVERIFY_STATUS", "Autoverify status", "Autoverification status or decision."),
            TagDefinition("LIMIT_OF_AGREEMENT", "Limit of agreement", "Bland-Altman limit of agreement."),
            TagDefinition("REFERENCE_LIMIT_SOURCE", "Reference limit source", "Source of reference limits."),
            TagDefinition("PEER_GROUP", "Peer group", "External quality assessment peer group."),
            TagDefinition("REPLICATE", "Replicate", "Replicate measurement number."),
            TagDefinition("LEVEL", "Level", "Method-validation concentration level."),
            TagDefinition("BATCH", "Batch", "Analytical batch or run identifier."),
        ),
    ),
    TagDefinition(
        "STUDY",
        "Study and cohort management",
        "Research and analysis inclusion/exclusion metadata.",
        (
            TagDefinition("STUDY_ID", "Study ID", "Study/project identifier."),
            TagDefinition("DIAGNOSIS", "Diagnosis", "Diagnosis text or ICD code."),
            TagDefinition("EXCLUSION_REASON", "Exclusion reason", "Reason a row/patient/sample was excluded."),
            TagDefinition("INCLUDE", "Include", "Analysis inclusion flag."),
        ),
    ),
    TagDefinition(
        "VALUE_STATE",
        "Value state and validation",
        "Cell-state and validation policy tags.",
        (
            TagDefinition("REQUIRED", "Required", "ABSENT values are validation errors."),
            TagDefinition("NULLABLE", "Nullable", "NULL token is allowed."),
            TagDefinition("NULL_OK", "NULL allowed", "Alias-style policy tag for allowing NULL."),
            TagDefinition("EMPTY_OK", "Empty allowed", "EMPTY token is allowed."),
            TagDefinition("WS_OK", "Whitespace allowed", "Whitespace-only token is allowed."),
            TagDefinition("VALID", "Valid", "Derived validation status."),
            TagDefinition("INVALID", "Invalid", "Derived validation status."),
            TagDefinition("MISSING", "Missing", "Derived missingness status."),
        ),
    ),
    TagDefinition(
        "EDA",
        "Exploratory data analysis",
        "Tags used in generated EDA result TAME files and EDA summary tables.",
        (
            TagDefinition("EDA", "EDA", "Generated exploratory data analysis output."),
            TagDefinition("EDA_SUMMARY", "EDA summary", "Column-level EDA summary table."),
            TagDefinition("EDA_CATEGORY", "EDA category distribution", "Category count/percentage table."),
            TagDefinition("EDA_NUMERIC", "EDA numeric percentiles", "Numeric percentile and percentile-band table."),
            TagDefinition("EDA_RESULT_BY", "EDA result by group", "RESULT summary stratified by BY/group columns, including all-group rows."),
            TagDefinition("TABLE", "Table", "Generated table name."),
            TagDefinition("ROW_INDEX", "Row index", "Generated row index."),
            TagDefinition("FIELD", "Field", "Generated field name."),
            TagDefinition("VALUE", "Value", "Generated field value."),
            TagDefinition("STAT", "Statistic", "Statistic name."),
        ),
    ),
    TagDefinition(
        "MEDIA",
        "Media",
        "Image and binary payload tags.",
        (
            TagDefinition("IMAGE", "Image", "Image value."),
            TagDefinition("B64", "Base64", "Base64 image payload."),
            TagDefinition("PATH", "Path", "Image file path."),
        ),
    ),
)


def tag_catalog_payload(meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = [_definition_payload(definition) for definition in TAG_CATALOG]
    custom = custom_tag_payloads(meta)
    if custom:
        payload.append(
            {
                "name": "CUSTOM",
                "label": "Custom tags",
                "description": "User-defined tags from META[TAG_DEFINITIONS].",
                "children": custom,
            }
        )
    return payload


def flat_tag_names(meta: dict[str, Any] | None = None) -> list[str]:
    names: list[str] = []

    def visit(definitions: tuple[TagDefinition, ...]) -> None:
        for definition in definitions:
            if definition.children:
                visit(definition.children)
            else:
                names.append(definition.name)

    visit(TAG_CATALOG)
    for name in custom_tag_definitions(meta):
        if name not in names:
            names.append(name)
    return names


def custom_tag_definitions(meta: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    section = ci_get(meta, "TAG_DEFINITIONS", {}) if isinstance(meta, dict) else {}
    if not isinstance(section, dict):
        return {}
    definitions: dict[str, dict[str, Any]] = {}
    for name, config in section.items():
        if not isinstance(config, dict):
            continue
        normalized = normalize_tag(str(name))
        if normalized:
            definitions[normalized] = config
    return definitions


def custom_tag_payloads(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for name, config in custom_tag_definitions(meta).items():
        payloads.append(
            {
                "name": name,
                "label": str(ci_get(config, "LABEL", name)),
                "description": str(ci_get(config, "DESCRIPTION", "")),
                "inherits": _definition_inherits(config),
                "properties": _definition_properties(config),
                "children": [],
            }
        )
    return sorted(payloads, key=lambda item: item["name"])


def effective_tags(meta: dict[str, Any] | None, tags: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        for effective in _tag_lineage(meta, normalize_tag(str(tag))):
            if effective and effective not in seen:
                ordered.append(effective)
                seen.add(effective)
    return tuple(ordered)


def tag_inherits(meta: dict[str, Any] | None, tag: str, target: str) -> bool:
    target_tag = normalize_tag(target)
    return target_tag in _tag_lineage(meta, normalize_tag(tag))


def any_tag_inherits(meta: dict[str, Any] | None, tags: tuple[str, ...] | list[str], target: str) -> bool:
    return any(tag_inherits(meta, tag, target) for tag in tags)


def tag_property(meta: dict[str, Any] | None, tags: tuple[str, ...] | list[str], key: str, default: Any = None) -> Any:
    definitions = custom_tag_definitions(meta)
    normalized_key = str(key).upper()
    for tag in tags:
        for lineage_tag in _tag_lineage(meta, normalize_tag(str(tag)), include_self_only_first=True):
            config = definitions.get(lineage_tag)
            if not config:
                continue
            found = _property_from_config(config, normalized_key)
            if found is not None:
                return found
    return default


def tag_property_int(meta: dict[str, Any] | None, tags: tuple[str, ...] | list[str], key: str, default: int) -> int:
    value = tag_property(meta, tags, key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _definition_payload(definition: TagDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "label": definition.label,
        "description": definition.description,
        "children": [_definition_payload(child) for child in definition.children],
    }


def _tag_lineage(
    meta: dict[str, Any] | None,
    tag: str,
    *,
    include_self_only_first: bool = False,
    _seen: set[str] | None = None,
) -> tuple[str, ...]:
    normalized = normalize_tag(tag)
    if not normalized:
        return ()
    seen = _seen or set()
    if normalized in seen:
        return ()
    seen.add(normalized)

    lineage = [normalized]
    definitions = custom_tag_definitions(meta)
    builtin_parents = _builtin_parent_tags(normalized)
    for parent in builtin_parents:
        lineage.extend(_tag_lineage(meta, parent, _seen=seen))
    config = definitions.get(normalized)
    if not config:
        return tuple(lineage)

    for parent in _definition_inherits(config):
        lineage.extend(_tag_lineage(meta, parent, _seen=seen))
        if include_self_only_first:
            include_self_only_first = False
    return tuple(lineage)


def _builtin_parent_tags(normalized: str) -> list[str]:
    parents = list(BUILTIN_TAG_PARENTS.get(normalized, ()))
    parts = parameterized_tag_parts(normalized)
    if parts is not None:
        base, qualifier = parts
        parents.append(base)
        parents.extend(PARAMETERIZED_TAG_PARENTS.get((base, qualifier), ()))
    return parents


def _definition_inherits(config: dict[str, Any]) -> list[str]:
    values = ci_get(config, "INHERITS", ci_get(config, "EXTENDS", ci_get(config, "IS_A", [])))
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return [normalize_tag(str(value)) for value in values if normalize_tag(str(value))]


def _definition_properties(config: dict[str, Any]) -> dict[str, Any]:
    ignored = {"LABEL", "DESCRIPTION", "INHERITS", "EXTENDS", "IS_A", "CHILDREN"}
    properties: dict[str, Any] = {}
    for key, value in config.items():
        normalized = str(key).upper()
        if normalized in ignored:
            continue
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                properties[str(nested_key).upper()] = nested_value
        else:
            properties[normalized] = value
    return properties


def _property_from_config(config: dict[str, Any], normalized_key: str) -> Any:
    direct = ci_get(config, normalized_key, None)
    if direct is not None:
        return direct
    for section_name in ("EDA", "ANALYSIS", "PROPERTIES"):
        section = ci_get(config, section_name, {})
        if isinstance(section, dict):
            value = ci_get(section, normalized_key, None)
            if value is not None:
                return value
    return None
