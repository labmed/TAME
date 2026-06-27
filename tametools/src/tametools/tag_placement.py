from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd

from .metadata import extract_column_tags, remove_legacy_tag_section
from .models import OperationOutput, TameDataset
from .tags import merge_tags, parse_header
from .toml_compat import dumps as dumps_toml


COLUMN_SECTION_NAME = "COLUMN"


def tags_to_meta(dataset: TameDataset) -> OperationOutput:
    result = dataset_with_tags_in_meta(dataset)
    return OperationOutput(
        name="tags-to-meta",
        dataset=result,
        table=tag_placement_table(dataset, mode="meta"),
        message=f"columns={len(dataset.columns)} tag_storage=meta",
    )


def tags_to_header(dataset: TameDataset) -> OperationOutput:
    result, warnings = dataset_with_tags_in_headers(dataset)
    return OperationOutput(
        name="tags-to-header",
        dataset=result,
        table=tag_placement_table(dataset, mode="header"),
        warnings=warnings,
        message=f"columns={len(dataset.columns)} tag_storage=header",
    )


def dataset_with_tags_in_meta(dataset: TameDataset) -> TameDataset:
    meta = _meta_with_materialized_tags(dataset)
    raw_sections = dict(dataset.raw_sections)
    raw_sections["META"] = dumps_toml(meta)
    return dataset.replace(meta=meta, raw_sections=raw_sections)


def dataset_with_tags_in_headers(dataset: TameDataset) -> tuple[TameDataset, list[str]]:
    meta, warnings = _meta_without_known_tags(dataset)
    raw_sections = dict(dataset.raw_sections)
    raw_sections["META"] = dumps_toml(meta)
    return dataset.replace(meta=meta, raw_sections=raw_sections), warnings


def tag_placement_table(dataset: TameDataset, *, mode: str) -> pd.DataFrame:
    existing = _extract_meta_tags(dataset.meta)
    rows: list[dict[str, Any]] = []
    for column in dataset.columns:
        _raw_name, header_tags = parse_header(column.original_header)
        meta_tags = _meta_tags_for_column(existing, column.name, column.original_header)
        effective_tags = column.tags
        rows.append(
            {
                "column": column.name,
                "meta_tags_before": "::".join(meta_tags),
                "header_tags_before": "::".join(header_tags),
                "effective_tags": "::".join(effective_tags),
                "meta_tags_after": "::".join(effective_tags) if mode == "meta" else "",
                "header_tags_after": "" if mode == "meta" else "::".join(effective_tags),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "column",
            "meta_tags_before",
            "header_tags_before",
            "effective_tags",
            "meta_tags_after",
            "header_tags_after",
        ],
    )


def _meta_with_materialized_tags(dataset: TameDataset) -> dict[str, Any]:
    meta = remove_legacy_tag_section(deepcopy(dataset.meta))
    existing = _extract_meta_tags(dataset.meta)
    column_section = dict(meta.get(COLUMN_SECTION_NAME, {})) if isinstance(meta.get(COLUMN_SECTION_NAME, {}), dict) else {}
    matched_existing = _matched_existing_tag_keys(dataset, existing)

    for column in dataset.columns:
        existing_tags = _meta_tags_for_column(existing, column.name, column.original_header)
        tags = merge_tags(existing_tags, column.tags)
        if tags:
            entry = dict(column_section.get(column.name, {})) if isinstance(column_section.get(column.name, {}), dict) else {}
            entry["TAGS"] = list(tags)
            column_section[column.name] = entry

    for name, tags in existing.items():
        if name not in matched_existing and tags:
            entry = dict(column_section.get(name, {})) if isinstance(column_section.get(name, {}), dict) else {}
            entry["TAGS"] = list(tags)
            column_section[name] = entry

    if column_section:
        meta[COLUMN_SECTION_NAME] = column_section
    return meta


def _meta_without_known_tags(dataset: TameDataset) -> tuple[dict[str, Any], list[str]]:
    meta = remove_legacy_tag_section(deepcopy(dataset.meta))
    existing = _extract_meta_tags(dataset.meta)
    matched_existing = _matched_existing_tag_keys(dataset, existing)
    remaining = {name: list(tags) for name, tags in existing.items() if name not in matched_existing and tags}
    warnings: list[str] = []
    column_section = dict(meta.get(COLUMN_SECTION_NAME, {})) if isinstance(meta.get(COLUMN_SECTION_NAME, {}), dict) else {}

    for name in matched_existing:
        entry = column_section.get(name)
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        for key in list(entry):
            if str(key).upper() == "TAGS":
                entry.pop(key, None)
        if entry:
            column_section[name] = entry
        else:
            column_section.pop(name, None)

    if remaining:
        for name, tags in remaining.items():
            entry = dict(column_section.get(name, {})) if isinstance(column_section.get(name, {}), dict) else {}
            entry["TAGS"] = tags
            column_section[name] = entry
        meta[COLUMN_SECTION_NAME] = column_section
        warnings.append("Preserved META[COLUMN] tag entries that do not match current DATA columns.")
    elif column_section:
        meta[COLUMN_SECTION_NAME] = column_section
    else:
        meta.pop(COLUMN_SECTION_NAME, None)

    return meta, warnings


def _matched_existing_tag_keys(dataset: TameDataset, existing: dict[str, tuple[str, ...]]) -> set[str]:
    matched: set[str] = set()
    for column in dataset.columns:
        raw_name, _header_tags = parse_header(column.original_header)
        for candidate in (column.name, raw_name):
            if candidate in existing:
                matched.add(candidate)
    return matched


def _meta_tags_for_column(existing: dict[str, tuple[str, ...]], column_name: str, original_header: str) -> tuple[str, ...]:
    raw_name, _header_tags = parse_header(original_header)
    if column_name in existing:
        return existing[column_name]
    return existing.get(raw_name, ())


def _extract_meta_tags(meta: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return extract_column_tags(meta)
