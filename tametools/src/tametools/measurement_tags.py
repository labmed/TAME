"""Explicit contracts for measurement annotations and categorical scales."""
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import ValidationIssue
from .tags import normalize_tag, parameterized_tag_parts


def is_categorical_measurement(dataset, column):
    return dataset.column_has_any_tag(column, ("NOMINAL", "ORDINAL"))


def require_measurement_tags(dataset):
    from .observation_contract import require_capabilities
    require_capabilities(dataset)
    issues = measurement_tag_issues(dataset)
    if issues:
        raise ValueError("Invalid measurement declaration: " + issues[0].message)


def require_quantitative(dataset, column, *, require_unit=False):
    if is_categorical_measurement(dataset, column):
        raise ValueError(f"{column.name}: NOMINAL/ORDINAL codes are not quantitative measurements")
    if not dataset.column_has_any_tag(column, ("NUM", "<NUM>")):
        raise ValueError(f"{column.name}: a quantitative measurement requires NUM or <NUM>")
    if require_unit:
        unit = ci_get(dataset.column_metadata(column), "UNIT", None)
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"{column.name}: a quantitative measurement requires a declared UNIT")
        unit_id = ci_get(dataset.column_metadata(column), "UNIT_ID", None)
        if unit_id is not None:
            from .analysis_contract import resolve_id
            row_units = dataset.df[resolve_id(dataset, unit_id).name]
            if not row_units.eq(unit).all():
                raise ValueError("Heterogeneous/missing row UNIT: split and integrate explicitly before analysis")


def measurement_tag_issues(dataset):
    issues = []
    for column in dataset.columns:
        def issue(message):
            issues.append(ValidationIssue(0, column.name, "MEASUREMENT_TAG", None, message))

        nominal = dataset.column_has_tag(column, "NOMINAL")
        ordinal = dataset.column_has_tag(column, "ORDINAL")
        if nominal and ordinal:
            issue("Declare NOMINAL or ORDINAL, not both")
        if (nominal or ordinal) and dataset.column_has_tag(column, "<NUM>"):
            issue("Categorical codes cannot be comparator concentrations")
        metadata = dataset.column_metadata(column)
        if ordinal:
            levels = ci_get(metadata, "LEVELS", None)
            if (not isinstance(levels, list) or not levels or
                    any(not isinstance(v, str) or not v.strip() for v in levels) or len(set(levels)) != len(levels)):
                issue("ORDINAL requires distinct, nonempty string COLUMN.LEVELS in declared order")
            else:
                for index, value in enumerate(dataset.df[column.name], 1):
                    if cell_state(value) == STATE_VALUE and str(value) not in levels:
                        issues.append(ValidationIssue(index + 1, column.name, "ORDINAL", value,
                                                      "Value is not in the declared ordinal LEVELS"))
        annotations = {}
        for tag in dataset.effective_column_tags(column):
            parts = parameterized_tag_parts(tag)
            if parts and parts[0] in {"ANALYTE", "SPECIMEN"}:
                annotations.setdefault(parts[0], set()).add(parts[1])
        measurement = ci_get(metadata, "MEASUREMENT", {})
        if (nominal or ordinal) and isinstance(measurement, dict) and ci_get(measurement, "SCALE", None) == "Qn":
            issue("NOMINAL/ORDINAL contradicts quantitative MEASUREMENT.SCALE Qn")
        for base, values in annotations.items():
            if len(values) > 1:
                issue(f"Conflicting {base} annotations on one column")
            field = "COMPONENT" if base == "ANALYTE" else "SPECIMEN"
            declared = ci_get(measurement, field, None) if isinstance(measurement, dict) else None
            if declared is not None and {normalize_tag(f"{base}({declared})")} != {normalize_tag(f"{base}({v})") for v in values}:
                issue(f"{base} tag contradicts COLUMN.MEASUREMENT.{field}")
    return issues
