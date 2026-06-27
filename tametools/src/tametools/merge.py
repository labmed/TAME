from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from .cellstate import STATE_ABSENT, cell_state
from .models import ColumnSpec, MergeResult, TameDataset
from .pandas_compat import concat_dataframes
from .tags import build_header, merge_tags, normalize_tag
from .transforms import harmonize_comparator_thresholds, split_comparator_columns


def merge_datasets(
    datasets: Iterable[TameDataset],
    *,
    source_labels: Iterable[str] | None = None,
    add_source_column: bool = True,
    source_column_name: str = "source_dataset",
    source_column_tags: Iterable[str] | None = ("BY", "STR", "SOURCE"),
    numeric_conflict: str = "promote",
    prefer_tag_names: bool = True,
) -> MergeResult:
    dataset_list = list(datasets)
    if not dataset_list:
        raise ValueError("At least one dataset is required.")

    labels = list(source_labels or _default_labels(dataset_list))
    if len(labels) != len(dataset_list):
        raise ValueError("source_labels must have the same length as datasets.")

    numeric_policy = numeric_conflict.lower()
    if numeric_policy not in {"strict", "promote", "split", "harmonize"}:
        raise ValueError("numeric_conflict must be one of: strict, promote, split, harmonize")

    warnings: list[str] = []
    merge_keys = [_dataset_merge_keys(dataset) for dataset in dataset_list]
    grouped_specs: dict[str, list[ColumnSpec]] = defaultdict(list)
    for dataset, key_map in zip(dataset_list, merge_keys):
        for column in dataset.columns:
            grouped_specs[key_map[column.name]].append(column)

    canonical_columns: list[ColumnSpec] = []
    key_to_column_name: dict[str, str] = {}
    for merge_key, specs in grouped_specs.items():
        canonical = _canonical_column(merge_key, specs, numeric_policy, warnings, prefer_tag_names=prefer_tag_names)
        key_to_column_name[merge_key] = canonical.name
        canonical_columns.append(ColumnSpec(original_header=canonical.original_header, name=canonical.name, tags=canonical.tags))

    merged_frames: list[pd.DataFrame] = []
    for dataset, label, key_map in zip(dataset_list, labels, merge_keys):
        frame = pd.DataFrame(index=dataset.df.index)
        for merge_key, target_name in key_to_column_name.items():
            local_name = next((name for name, key in key_map.items() if key == merge_key and name in dataset.df.columns), None)
            frame[target_name] = dataset.df[local_name] if local_name is not None else None
        if add_source_column:
            frame[source_column_name] = label
        merged_frames.append(frame)

    final_columns = canonical_columns.copy()
    if add_source_column:
        final_columns.append(
            ColumnSpec(
                original_header=build_header(source_column_name, source_column_tags or ("BY", "STR", "SOURCE")),
                name=source_column_name,
                tags=merge_tags(source_column_tags or ("BY", "STR", "SOURCE")),
            )
        )

    merged_df = concat_dataframes(merged_frames, ignore_index=True)
    merged_df = merged_df[[column.name for column in final_columns]]
    merged = dataset_list[0].replace(df=merged_df, columns=final_columns, source_path=None)

    if numeric_policy == "split":
        conflict_columns = [column.name for column in final_columns if column.has_tag("<NUM>") and any(warning.startswith(f"NUM_CONFLICT {column.name}") for warning in warnings)]
        if conflict_columns:
            merged = split_comparator_columns(merged, target_columns=conflict_columns, target_tags=("<NUM>",), drop_original=False)
    elif numeric_policy == "harmonize":
        cnum_columns = [column.name for column in final_columns if column.has_tag("<NUM>")]
        if cnum_columns:
            merged, preview = harmonize_comparator_thresholds(merged, target_columns=cnum_columns)
            if not preview.empty:
                changed_rows = int(preview["changed_rows"].sum())
                if changed_rows > 0:
                    warnings.append(f"HARMONIZE_APPLIED changed_rows={changed_rows}")

    return MergeResult(dataset=merged, warnings=warnings)


def _default_labels(datasets: list[TameDataset]) -> list[str]:
    labels: list[str] = []
    for index, dataset in enumerate(datasets, start=1):
        if dataset.source_path:
            labels.append(Path(dataset.source_path).stem)
        else:
            labels.append(f"dataset_{index}")
    return labels


def _dataset_merge_keys(dataset: TameDataset) -> dict[str, str]:
    signature_counts = Counter(_semantic_signature(column) for column in dataset.columns if column.tags)
    keys: dict[str, str] = {}
    for column in dataset.columns:
        signature = _semantic_signature(column)
        if signature and signature_counts[signature] == 1:
            keys[column.name] = f"tag::{signature}"
        else:
            keys[column.name] = f"name::{column.name}"
    return keys


def _canonical_column(
    merge_key: str,
    specs: list[ColumnSpec],
    numeric_policy: str,
    warnings: list[str],
    *,
    prefer_tag_names: bool,
) -> ColumnSpec:
    names = [spec.name for spec in specs]
    tags = merge_tags(*(spec.tags for spec in specs))
    counts = Counter(names)
    name = counts.most_common(1)[0][0]

    if len(set(names)) > 1 and merge_key.startswith("tag::"):
        if prefer_tag_names:
            name = _canonical_name_from_tags(tags, fallback=name)
            warnings.append(f"NAME_MISMATCH {merge_key} -> using tag_name '{name}' from {sorted(set(names))}")
        else:
            warnings.append(f"NAME_MISMATCH {merge_key} -> using '{name}' from {sorted(set(names))}")

    has_num = any(spec.has_tag("NUM") for spec in specs)
    has_cnum = any(spec.has_tag("<NUM>") for spec in specs)
    if has_num and has_cnum:
        warning = (
            f"NUM_CONFLICT {name}: datasets contain both NUM and <NUM> representations; "
            f"policy={numeric_policy}"
        )
        warnings.append(warning)
        if numeric_policy == "strict":
            raise ValueError(warning)
        if numeric_policy in {"promote", "split", "harmonize"}:
            tags = tuple(tag for tag in tags if tag != "NUM")
            tags = merge_tags(tags, ("<NUM>",))

    return ColumnSpec(
        original_header=build_header(name, tags),
        name=name,
        tags=tags,
    )


def _semantic_signature(column: ColumnSpec) -> str:
    normalized: list[str] = []
    for tag in column.tags:
        if tag in {"NUM", "<NUM>"}:
            normalized.append("NUMLIKE")
        else:
            normalized.append(tag)
    return "::".join(normalized)


def drop_absent_only_columns(frame: pd.DataFrame, columns: list[ColumnSpec]) -> tuple[pd.DataFrame, list[ColumnSpec]]:
    keep_columns: list[ColumnSpec] = []
    keep_names: list[str] = []
    for column in columns:
        states = frame[column.name].map(cell_state)
        if (states != STATE_ABSENT).any():
            keep_columns.append(column)
            keep_names.append(column.name)
    return frame[keep_names].copy(), _reindex_columns(keep_columns)


def _canonical_name_from_tags(tags: tuple[str, ...], *, fallback: str) -> str:
    ignored = {
        "BY",
        "STR",
        "NUM",
        "<NUM>",
        "TXT",
        "CAT",
        "CMP",
        "NULLABLE",
        "NULL_OK",
        "EMPTY_OK",
        "WS_OK",
        "REQUIRED",
    }
    candidates = [normalize_tag(tag) for tag in tags if normalize_tag(tag) not in ignored]
    if not candidates:
        return fallback
    return candidates[0]


def _reindex_columns(columns: list[ColumnSpec]) -> list[ColumnSpec]:
    normalized: list[ColumnSpec] = []
    for column in columns:
        normalized.append(
            ColumnSpec(
                original_header=column.original_header,
                name=column.name,
                tags=column.tags,
            )
        )
    return normalized
