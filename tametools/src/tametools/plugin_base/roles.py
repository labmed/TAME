from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..config import ci_get
from ..models import ColumnSpec, TameDataset
from ..measurement_tags import is_categorical_measurement, require_measurement_tags
from ..analysis_contract import resolve_id
from ..tags import TAG_TOKEN_RE, normalize_tag


@dataclass(frozen=True)
class PluginRole:
    name: str
    tags: tuple[str, ...]
    cardinality: str = "one"
    iteration: str = "single"
    numeric: bool = False
    option_keys: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class RoleBinding:
    role: PluginRole
    columns: tuple[ColumnSpec, ...]
    warnings: tuple[str, ...] = ()

    @property
    def first(self) -> ColumnSpec | None:
        return self.columns[0] if self.columns else None

    @property
    def is_multi(self) -> bool:
        return len(self.columns) > 1


RESULT_ROLE = PluginRole(
    name="result",
    tags=("RESULT", "NUM", "<NUM>"),
    cardinality="one_or_more",
    iteration="per_result",
    numeric=True,
    option_keys=("RESULT", "RESULT_COLUMN", "SCORE"),
    aliases=("NUM", "<NUM>"),
    description="Measured result columns. Multiple columns are analysed independently.",
)


def bind_role(dataset: TameDataset, role: PluginRole, options: dict[str, Any] | None = None) -> RoleBinding:
    options = options or {}
    require_measurement_tags(dataset)
    explicit = _explicit_columns(dataset, role, options)
    if explicit:
        columns = explicit
    else:
        columns = _tag_columns(dataset, role)

    warnings: list[str] = []
    if not columns and role.cardinality in {"one", "one_or_more"}:
        warnings.append(f"Missing required role {role.name}: tags={','.join(role.tags)}.")
    if len(columns) > 1 and role.cardinality == "one":
        raise ValueError(f"Role {role.name} requires one column but matched {len(columns)}: {_names(columns)}.")
    return RoleBinding(role=role, columns=tuple(columns), warnings=tuple(warnings))


def result_binding(dataset: TameDataset, options: dict[str, Any] | None = None) -> RoleBinding:
    return bind_role(dataset, RESULT_ROLE, options)


def result_columns(dataset: TameDataset, options: dict[str, Any] | None = None) -> list[ColumnSpec]:
    return list(result_binding(dataset, options).columns)


def result_source_record(column: ColumnSpec, *, include: bool) -> dict[str, str]:
    return {"source_result_column": column.name} if include else {}


def result_test_name(dataset: TameDataset, result_column: ColumnSpec, test_column: ColumnSpec | None, row_index: Any) -> str:
    if test_column is None:
        return result_column.name
    value = dataset.df.loc[row_index, test_column.name]
    if value is None:
        return result_column.name
    try:
        import pandas as pd

        if pd.isna(value):
            return result_column.name
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or result_column.name


def role_contract_payload(roles: Iterable[PluginRole]) -> list[dict[str, Any]]:
    return [
        {
            "name": role.name,
            "tags": list(role.tags),
            "cardinality": role.cardinality,
            "iteration": role.iteration,
            "numeric": role.numeric,
            "option_keys": list(role.option_keys),
            "aliases": list(role.aliases),
            "description": role.description,
        }
        for role in roles
    ]


def _explicit_columns(dataset: TameDataset, role: PluginRole, options: dict[str, Any]) -> list[ColumnSpec]:
    result: list[ColumnSpec] = []
    special = [key for key in ("RESULT_IDS", "RESULT_TAGS") if any(str(k).upper() == key for k in options)] if role.name == "result" else []
    regular = [key for key in role.option_keys if any(str(k).upper() == key for k in options)]
    if len(special) > 1 or len(regular) > 1 or (special and regular):
        raise ValueError("Use only one result selector: RESULT_IDS, RESULT_TAGS, or a result column option")
    if special:
        key = special[0]
        values = ci_get(options, key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values) or len(set(values)) != len(values):
            raise ValueError(key + " requires a nonempty distinct string list")
        if key == "RESULT_IDS":
            result = [resolve_id(dataset, identifier) for identifier in values]
        else:
            from ..analysis_contract import column_ids
            column_ids(dataset)
            if any(not TAG_TOKEN_RE.fullmatch(v) for v in values) or len({normalize_tag(v) for v in values}) != len(values):
                raise ValueError("Invalid or duplicate RESULT_TAGS")
            result = dataset.find_columns(required_tags=values)
            if any(ci_get(dataset.column_metadata(c), "ID", None) is None for c in result):
                raise ValueError("RESULT_TAGS matches require stable COLUMN.ID")
            result.sort(key=lambda c: ci_get(dataset.column_metadata(c), "ID"))
        if not result or len(_filter_numeric(dataset, role, result)) != len(result):
            raise ValueError("Explicit result selection is empty or not quantitative")
        return result
    for key in role.option_keys:
        if key not in regular:
            continue
        value = ci_get(options, key, None)
        items = _as_list(value)
        if not items:
            raise ValueError(key + " was specified but is empty")
        for item in items:
            column = _resolve_column(dataset, item)
            if column is None:
                raise ValueError(f"{key}: unknown or ambiguous column selector {item!r}")
            if role.numeric and not _filter_numeric(dataset, role, [column]):
                raise ValueError(f"{key}: selected column is not quantitative: {column.name}")
            if column.name not in {existing.name for existing in result}:
                result.append(column)
    return _filter_numeric(dataset, role, result)


def _tag_columns(dataset: TameDataset, role: PluginRole) -> list[ColumnSpec]:
    if role.name == "result":
        result_matches = [
            column
            for column in dataset.columns_with_tag("RESULT")
            if dataset.column_has_tag(column, "NUM") or dataset.column_has_tag(column, "<NUM>") or dataset.column_has_tag(column, "RESULT")
        ]
        if result_matches:
            return _filter_numeric(dataset, role, result_matches)

    matched: list[ColumnSpec] = []
    for tag in role.tags:
        for column in dataset.columns_with_tag(tag):
            if column.name not in {existing.name for existing in matched}:
                matched.append(column)
    return _filter_numeric(dataset, role, matched)


def _filter_numeric(dataset: TameDataset, role: PluginRole, columns: list[ColumnSpec]) -> list[ColumnSpec]:
    if not role.numeric:
        return columns
    return [
        column
        for column in columns
        if not is_categorical_measurement(dataset, column) and (dataset.column_has_tag(column, "NUM")
        or dataset.column_has_tag(column, "<NUM>")
        or dataset.column_has_tag(column, "RESULT"))
    ]


def _resolve_column(dataset: TameDataset, value: Any) -> ColumnSpec | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower().startswith("id:"):
        return resolve_id(dataset, text[3:])
    if text.lower().startswith("tag:"):
        matches = dataset.columns_with_tag(text.split(":", 1)[1])
        return matches[0] if len(matches) == 1 else None
    for column in dataset.columns:
        if column.name == text:
            return column
    lowered = text.lower()
    matches = [column for column in dataset.columns if column.name.lower() == lowered]
    return matches[0] if len(matches) == 1 else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    return [text] if text else []


def _names(columns: Iterable[ColumnSpec]) -> str:
    return ", ".join(column.name for column in columns)
