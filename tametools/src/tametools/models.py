from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from .cellstate import cell_state
from .metadata import analysis_settings, column_metadata_for
from .tag_catalog import any_tag_inherits, effective_tags
from .tags import build_header, has_all_tags, has_any_tag, merge_tags, normalize_tag


@dataclass(frozen=True, init=False)
class ColumnSpec:
    original_header: str
    name: str
    tags: tuple[str, ...] = ()

    def __init__(
        self,
        original_header: str,
        name: str,
        tags: Iterable[str] = (),
        *,
        index: int | None = None,
    ) -> None:
        object.__setattr__(self, "original_header", str(original_header))
        object.__setattr__(self, "name", str(name))
        object.__setattr__(self, "tags", merge_tags(tags))

    def has_tag(self, tag: str) -> bool:
        return normalize_tag(tag) in self.tags

    def has_all_tags(self, tags: Iterable[str]) -> bool:
        return has_all_tags(self.tags, tags)

    def has_any_tag(self, tags: Iterable[str]) -> bool:
        return has_any_tag(self.tags, tags)

    @property
    def tagged_header(self) -> str:
        return build_header(self.name, self.tags)


@dataclass(frozen=True)
class ValidationIssue:
    row_number: int
    column: str
    tag: str
    value: Any
    message: str
    severity: str = "error"


@dataclass
class ValidationResult:
    issues: list[ValidationIssue]
    cleaned_dataset: "TameDataset"


@dataclass
class OperationOutput:
    name: str
    dataset: "TameDataset | None" = None
    table: pd.DataFrame | None = None
    tables: dict[str, pd.DataFrame] | None = None
    charts: list[dict[str, Any]] | None = None
    files: list[str] | None = None
    issues: list[ValidationIssue] | None = None
    warnings: list[str] | None = None
    message: str | None = None
    status: str = "SUCCEEDED"


@dataclass
class PipelineResult:
    work_name: str
    final_dataset: "TameDataset"
    outputs: list[OperationOutput]


@dataclass
class MergeResult:
    dataset: "TameDataset"
    warnings: list[str]


@dataclass
class EDAReport:
    summary: pd.DataFrame
    category_distribution: pd.DataFrame
    numeric_percentiles: pd.DataFrame
    numeric_percentile_bands: pd.DataFrame
    result_by_summary: pd.DataFrame
    comparator_profile: pd.DataFrame
    comparator_policy_impact: pd.DataFrame
    harmonization_preview: pd.DataFrame
    warnings: list[str]


@dataclass(frozen=True)
class ReferenceIntervalPlan:
    result_columns: tuple[ColumnSpec, ...]
    item_column: ColumnSpec | None
    age_column: ColumnSpec | None
    sex_column: ColumnSpec | None
    by_columns: tuple[ColumnSpec, ...]


@dataclass
class TameDataset:
    df: pd.DataFrame
    columns: list[ColumnSpec]
    meta: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] = field(default_factory=dict)
    job: dict[str, Any] = field(default_factory=dict)
    raw_sections: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None

    def __post_init__(self) -> None:
        expected = [column.name for column in self.columns]
        if list(self.df.columns) != expected:
            self.df.columns = expected

    def settings(self) -> dict[str, Any]:
        return analysis_settings(self.meta)

    def column_metadata(self, column: ColumnSpec | str) -> dict[str, Any]:
        name = column.name if isinstance(column, ColumnSpec) else str(column)
        return column_metadata_for(self.meta, name)

    def tagged_headers(self) -> list[str]:
        return [column.tagged_header for column in self.columns]

    def columns_with_tag(self, tag: str) -> list[ColumnSpec]:
        normalized = normalize_tag(tag)
        return [column for column in self.columns if any_tag_inherits(self.meta, column.tags, normalized)]

    def first_column_with_tag(self, tag: str) -> ColumnSpec | None:
        columns = self.columns_with_tag(tag)
        return columns[0] if columns else None

    def column_has_tag(self, column: ColumnSpec, tag: str) -> bool:
        return any_tag_inherits(self.meta, column.tags, tag)

    def column_has_any_tag(self, column: ColumnSpec, tags: Iterable[str]) -> bool:
        return any(self.column_has_tag(column, tag) for tag in tags)

    def effective_column_tags(self, column: ColumnSpec) -> tuple[str, ...]:
        return effective_tags(self.meta, column.tags)

    def find_columns(
        self,
        *,
        required_tags: Iterable[str] | None = None,
        any_tags: Iterable[str] | None = None,
        exclude_tags: Iterable[str] | None = None,
    ) -> list[ColumnSpec]:
        required = tuple(required_tags or ())
        any_of = tuple(any_tags or ())
        exclude = {normalize_tag(tag) for tag in (exclude_tags or ())}
        result: list[ColumnSpec] = []
        for column in self.columns:
            effective = self.effective_column_tags(column)
            if required and not has_all_tags(effective, required):
                continue
            if any_of and not has_any_tag(effective, any_of):
                continue
            if exclude and any(tag in exclude for tag in effective):
                continue
            result.append(column)
        return result

    def column_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "index": list(range(len(self.columns))),
                "name": [column.name for column in self.columns],
                "original_header": [column.original_header for column in self.columns],
                "tags": ["::".join(column.tags) for column in self.columns],
            }
        )

    def state_frame(self) -> pd.DataFrame:
        data = {column.name: self.df[column.name].map(cell_state) for column in self.columns}
        return pd.DataFrame(data, index=self.df.index)

    def with_df(self, df: pd.DataFrame) -> "TameDataset":
        return self.replace(df=df)

    def replace(
        self,
        *,
        df: pd.DataFrame | None = None,
        columns: list[ColumnSpec] | None = None,
        meta: dict[str, Any] | None = None,
        schema: dict[str, Any] | None = None,
        job: dict[str, Any] | None = None,
        raw_sections: dict[str, str] | None = None,
        source_path: str | None = None,
    ) -> "TameDataset":
        return TameDataset(
            df=df if df is not None else self.df,
            columns=list(columns) if columns is not None else self.columns,
            meta=dict(meta) if meta is not None else self.meta,
            schema=dict(schema) if schema is not None else self.schema,
            job=dict(job) if job is not None else self.job,
            raw_sections=dict(raw_sections) if raw_sections is not None else self.raw_sections,
            source_path=self.source_path if source_path is None else source_path,
        )


def merged_column_specs(
    headers: list[str],
    meta_tags: dict[str, tuple[str, ...]] | None = None,
    schema_tags: dict[str, tuple[str, ...]] | None = None,
) -> list[ColumnSpec]:
    meta_tags = meta_tags or {}
    schema_tags = schema_tags or {}
    parsed_headers: list[tuple[str, tuple[str, ...]]] = [_parse_header(header) for header in headers]
    unique_names = make_unique_column_names([name for name, _ in parsed_headers])
    columns: list[ColumnSpec] = []
    for index, (header, parsed) in enumerate(zip(headers, parsed_headers)):
        name, header_tags = parsed
        unique_name = unique_names[index]
        tags = merge_tags(schema_tags.get(name, ()), meta_tags.get(name, ()), header_tags)
        columns.append(
            ColumnSpec(
                original_header=str(header),
                name=unique_name,
                tags=tags,
            )
        )
    return columns


def make_unique_column_names(names: Iterable[str]) -> list[str]:
    raw_name_list = [str(name) for name in names]
    raw_names = set(raw_name_list)
    used: set[str] = set()
    next_suffix: dict[str, int] = {}
    unique: list[str] = []

    for base in raw_name_list:
        candidate = base
        if candidate in used:
            suffix = next_suffix.get(base, 1)
            candidate = f"{base}.{suffix}"
            while candidate in used or candidate in raw_names:
                suffix += 1
                candidate = f"{base}.{suffix}"
            next_suffix[base] = suffix + 1
        else:
            next_suffix.setdefault(base, 1)
        used.add(candidate)
        unique.append(candidate)

    return unique


def _parse_header(header: str) -> tuple[str, tuple[str, ...]]:
    from .tags import parse_header

    return parse_header(header)
