from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from .cellstate import cell_state
from .config import ci_get
from .tags import build_header, has_all_tags, has_any_tag, merge_tags, normalize_tag


@dataclass(frozen=True)
class ColumnSpec:
    index: int
    original_header: str
    name: str
    tags: tuple[str, ...] = ()

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
    issues: list[ValidationIssue] | None = None
    warnings: list[str] | None = None
    message: str | None = None


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
    comparator_profile: pd.DataFrame
    comparator_policy_impact: pd.DataFrame
    harmonization_preview: pd.DataFrame
    warnings: list[str]


@dataclass(frozen=True)
class ReferenceIntervalPlan:
    result_columns: tuple[ColumnSpec, ...]
    item_column: ColumnSpec | None
    age_column: ColumnSpec | None
    gender_column: ColumnSpec | None
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
        section = ci_get(self.meta, "SETTINGS", {})
        return section if isinstance(section, dict) else {}

    def tagged_headers(self) -> list[str]:
        return [column.tagged_header for column in self.columns]

    def columns_with_tag(self, tag: str) -> list[ColumnSpec]:
        normalized = normalize_tag(tag)
        return [column for column in self.columns if normalized in column.tags]

    def first_column_with_tag(self, tag: str) -> ColumnSpec | None:
        columns = self.columns_with_tag(tag)
        return columns[0] if columns else None

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
            if required and not column.has_all_tags(required):
                continue
            if any_of and not column.has_any_tag(any_of):
                continue
            if exclude and any(tag in exclude for tag in column.tags):
                continue
            result.append(column)
        return result

    def column_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "index": [column.index for column in self.columns],
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
            df=(df if df is not None else self.df).copy(),
            columns=(columns if columns is not None else self.columns).copy(),
            meta=(meta if meta is not None else self.meta).copy(),
            schema=(schema if schema is not None else self.schema).copy(),
            job=(job if job is not None else self.job).copy(),
            raw_sections=(raw_sections if raw_sections is not None else self.raw_sections).copy(),
            source_path=self.source_path if source_path is None else source_path,
        )


def merged_column_specs(
    headers: list[str],
    meta_tags: dict[str, tuple[str, ...]] | None = None,
    schema_tags: dict[str, tuple[str, ...]] | None = None,
) -> list[ColumnSpec]:
    meta_tags = meta_tags or {}
    schema_tags = schema_tags or {}
    columns: list[ColumnSpec] = []
    for index, header in enumerate(headers):
        name, header_tags = _parse_header(header)
        tags = merge_tags(header_tags, meta_tags.get(name, ()), schema_tags.get(name, ()))
        columns.append(
            ColumnSpec(
                index=index,
                original_header=str(header),
                name=name,
                tags=tags,
            )
        )
    return columns


def _parse_header(header: str) -> tuple[str, tuple[str, ...]]:
    from .tags import parse_header

    return parse_header(header)
