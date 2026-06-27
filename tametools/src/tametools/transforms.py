from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Iterable

import pandas as pd

from .analysis import comparator_harmonization_preview, comparator_parts_series
from .cellstate import STATE_VALUE, cell_state
from .models import ColumnSpec, TameDataset
from .pandas_compat import concat_dataframes
from .tags import build_header, merge_tags


def anonymize_dataset(
    dataset: TameDataset,
    *,
    hash_columns: Iterable[str] | None = None,
    drop_columns: Iterable[str] | None = None,
    hash_tags: Iterable[str] | None = ("ID", "HOSPITAL_ID"),
    drop_tags: Iterable[str] | None = ("NAME",),
    salt: str = "",
    hash_prefix: str = "anon_",
) -> tuple[TameDataset, dict[str, pd.DataFrame]]:
    hash_columns = set(hash_columns or ())
    drop_columns = set(drop_columns or ())
    hash_tags = tuple(hash_tags or ())
    drop_tags = tuple(drop_tags or ())

    selected_hash = [
        column
        for column in dataset.columns
        if column.name in hash_columns or (hash_tags and dataset.column_has_any_tag(column, hash_tags))
    ]
    selected_drop = [
        column
        for column in dataset.columns
        if column.name in drop_columns or (drop_tags and dataset.column_has_any_tag(column, drop_tags))
    ]

    drop_names = {column.name for column in selected_drop}
    frame = dataset.df.copy()
    mapping_tables: dict[str, pd.DataFrame] = {}

    for column in selected_hash:
        mapping, series = _hash_series(frame[column.name], salt=salt, hash_prefix=hash_prefix)
        frame[column.name] = series
        mapping_tables[column.name] = mapping

    if drop_names:
        frame = frame.drop(columns=sorted(drop_names))

    columns = [column for column in dataset.columns if column.name not in drop_names]
    return dataset.replace(df=frame, columns=columns), mapping_tables


def write_mapping_tables(
    mapping_tables: dict[str, pd.DataFrame],
    output_dir: str | Path,
) -> pd.DataFrame:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, object]] = []
    used_names: set[str] = set()
    for column_name, table in mapping_tables.items():
        filename = _mapping_filename(column_name, used_names)
        table.to_csv(output_path / filename, index=False, encoding="utf-8")
        manifest_rows.append(
            {
                "column": column_name,
                "file": filename,
                "rows": len(table),
            }
        )

    manifest = pd.DataFrame(manifest_rows, columns=["column", "file", "rows"])
    if not manifest.empty:
        manifest.to_csv(output_path / "manifest.csv", index=False, encoding="utf-8")
    return manifest


def sample_dataset(
    dataset: TameDataset,
    *,
    rows: int | None = None,
    frac: float | None = None,
    seed: int | None = None,
    by_columns: Iterable[str] | None = None,
    by_tags: Iterable[str] | None = None,
    replace: bool = False,
) -> TameDataset:
    if (rows is None) == (frac is None):
        raise ValueError("Provide exactly one of rows or frac.")

    if rows is not None:
        rows = int(rows)
        if rows <= 0:
            raise ValueError("rows must be a positive integer.")

    if frac is not None:
        frac = float(frac)
        if frac <= 0:
            raise ValueError("frac must be greater than 0.")
        if frac > 1 and not replace:
            raise ValueError("frac greater than 1 requires replace=True.")

    frame = dataset.df.copy()
    if frame.empty:
        return dataset.replace(df=frame.reset_index(drop=True))

    group_columns = _sample_group_columns(dataset, by_columns=by_columns, by_tags=by_tags)
    working = frame.reset_index().rename(columns={"index": "__sample_order__"})

    if not group_columns:
        sampled = _sample_frame(working, rows=rows, frac=frac, seed=seed, replace=replace)
    else:
        parts: list[pd.DataFrame] = []
        for offset, (_, group) in enumerate(working.groupby(group_columns, sort=False, dropna=False, observed=False)):
            group_seed = None if seed is None else seed + offset
            parts.append(_sample_frame(group, rows=rows, frac=frac, seed=group_seed, replace=replace))
        sampled = concat_dataframes(parts, axis=0) if parts else working.iloc[0:0].copy()

    sampled = sampled.sort_values("__sample_order__", kind="stable").drop(columns=["__sample_order__"]).reset_index(drop=True)
    return dataset.replace(df=sampled)


def split_comparator_columns(
    dataset: TameDataset,
    *,
    target_columns: Iterable[str] | None = None,
    target_tags: Iterable[str] | None = ("<NUM>",),
    drop_original: bool = False,
) -> TameDataset:
    target_columns = set(target_columns or ())
    target_tags = tuple(target_tags or ())
    frame = dataset.df.copy()
    new_columns: list[ColumnSpec] = []
    selected: list[ColumnSpec] = []

    for column in dataset.columns:
        matches_name = not target_columns or column.name in target_columns
        matches_tag = not target_tags or dataset.column_has_any_tag(column, target_tags)
        if matches_name and matches_tag:
            selected.append(column)

    for column in dataset.columns:
        if column in selected and drop_original:
            continue
        new_columns.append(column)

        if column not in selected:
            continue

        parts = comparator_parts_series(dataset, column)
        op_name = f"{column.name}__cmp"
        value_name = f"{column.name}__num"
        frame[op_name] = parts["comparator_code"]
        frame[value_name] = parts["numeric_value"]

        op_tags = _comparator_op_tags(column)
        value_tags = _comparator_value_tags(column)
        new_columns.append(
            ColumnSpec(
                original_header=build_header(op_name, op_tags),
                name=op_name,
                tags=op_tags,
            )
        )
        new_columns.append(
            ColumnSpec(
                original_header=build_header(value_name, value_tags),
                name=value_name,
                tags=value_tags,
            )
        )

    if drop_original:
        for column in selected:
            frame = frame.drop(columns=[column.name])

    normalized_columns = _reindex_columns(new_columns)
    frame = frame[[column.name for column in normalized_columns]]
    return dataset.replace(df=frame, columns=normalized_columns)


def fix_num_comparator_values(
    dataset: TameDataset,
    *,
    handling: str = "delete",
    target_columns: Iterable[str] | None = None,
) -> tuple[TameDataset, pd.DataFrame]:
    mode = str(handling or "delete").strip().lower()
    if mode not in {"delete", "value"}:
        raise ValueError("handling must be one of: delete, value")

    requested = {str(name) for name in (target_columns or ()) if str(name).strip()}
    selected = [
        column
        for column in dataset.columns
        if dataset.column_has_tag(column, "NUM")
        and not dataset.column_has_tag(column, "<NUM>")
        and (not requested or column.name in requested)
    ]

    frame = dataset.df.copy()
    drop_mask = pd.Series(False, index=frame.index)
    rows: list[dict[str, object]] = []

    for column in selected:
        parts = comparator_parts_series(dataset, column)
        bounded = parts["comparator_code"].isin(["LT", "LE", "GT", "GE"])
        affected = int(bounded.sum())
        if affected == 0:
            continue

        if mode == "delete":
            drop_mask |= bounded
        else:
            frame.loc[bounded, column.name] = parts.loc[bounded, "numeric_value"].map(_format_threshold)

        rows.append(
            {
                "column": column.name,
                "handling": mode,
                "affected_cells": affected,
                "affected_rows": affected if mode == "value" else int(drop_mask.sum()),
            }
        )

    if mode == "delete" and bool(drop_mask.any()):
        frame = frame.loc[~drop_mask].reset_index(drop=True)

    table = pd.DataFrame(rows, columns=["column", "handling", "affected_cells", "affected_rows"])
    return dataset.replace(df=frame), table


def harmonize_comparator_thresholds(
    dataset: TameDataset,
    *,
    target_columns: Iterable[str] | None = None,
    target_tags: Iterable[str] | None = ("<NUM>",),
    exact_handling: str = "between",
) -> tuple[TameDataset, pd.DataFrame]:
    target_columns = set(target_columns or ())
    target_tags = tuple(target_tags or ())
    exact_mode = exact_handling.lower()
    if exact_mode not in {"between", "all"}:
        raise ValueError("exact_handling must be one of: between, all")

    preview = comparator_harmonization_preview(dataset)
    if preview.empty:
        return dataset, preview.assign(changed_rows=pd.Series(dtype="int64"))

    selected_columns = {
        column.name
        for column in dataset.columns
        if (not target_columns or column.name in target_columns)
        and (not target_tags or dataset.column_has_any_tag(column, target_tags))
    }
    if selected_columns:
        preview = preview.loc[preview["result_column"].isin(selected_columns)].copy()
    if preview.empty:
        return dataset, preview.assign(changed_rows=pd.Series(dtype="int64"))

    frame = dataset.df.copy()
    changed_rows: list[int] = []
    item_column = _item_column(dataset)

    for row in preview.itertuples(index=False):
        result_column = str(row.result_column)
        if result_column not in frame.columns:
            changed_rows.append(0)
            continue

        parts = comparator_parts_series(dataset, result_column)
        comparator_codes = parts["comparator_code"]
        numeric_values = parts["numeric_value"]
        raw_values = frame[result_column].copy()
        unified_threshold = float(row.unified_threshold)
        family = str(row.family)

        mask = pd.Series(True, index=frame.index)
        if "item" in preview.columns and _has_item_value(row.item) and item_column is not None:
            mask &= frame[item_column.name] == row.item

        bounded_codes = {"LT", "LE"} if family == "LT" else {"GT", "GE"}
        bounded_mask = mask & comparator_codes.isin(bounded_codes)

        if family == "LT":
            if exact_mode == "between":
                exact_mask = mask & (comparator_codes == "EQ") & (numeric_values >= _min_threshold(row.existing_thresholds)) & (numeric_values < unified_threshold)
            else:
                exact_mask = mask & (comparator_codes == "EQ") & (numeric_values < unified_threshold)
            unified_value = f"<{_format_threshold(unified_threshold)}"
        else:
            if exact_mode == "between":
                exact_mask = mask & (comparator_codes == "EQ") & (numeric_values <= _max_threshold(row.existing_thresholds)) & (numeric_values > unified_threshold)
            else:
                exact_mask = mask & (comparator_codes == "EQ") & (numeric_values > unified_threshold)
            unified_value = f">{_format_threshold(unified_threshold)}"

        frame.loc[bounded_mask | exact_mask, result_column] = unified_value
        changed_rows.append(int((bounded_mask | exact_mask).sum()))

    preview = preview.copy()
    preview["changed_rows"] = changed_rows
    return dataset.replace(df=frame), preview


def _hash_series(series: pd.Series, *, salt: str, hash_prefix: str) -> tuple[pd.DataFrame, pd.Series]:
    mapping: dict[str, str] = {}

    def _hash_value(value: object) -> object:
        if cell_state(value) != STATE_VALUE:
            return value
        text = str(value).strip()
        if not text:
            return value
        if text not in mapping:
            digest = hashlib.sha256(f"{salt}|{text}".encode("utf-8")).hexdigest()[:12].upper()
            mapping[text] = f"{hash_prefix}{digest}"
        return mapping[text]

    anonymized = series.map(_hash_value)
    mapping_frame = pd.DataFrame({"original": list(mapping.keys()), "anonymized": list(mapping.values())})
    return mapping_frame, anonymized


def _sample_group_columns(
    dataset: TameDataset,
    *,
    by_columns: Iterable[str] | None,
    by_tags: Iterable[str] | None,
) -> list[str]:
    requested_columns = [str(name) for name in (by_columns or ()) if str(name).strip()]
    requested_tags = tuple(str(tag) for tag in (by_tags or ()) if str(tag).strip())

    available = {column.name for column in dataset.columns}
    missing = [name for name in requested_columns if name not in available]
    if missing:
        raise KeyError(f"Unknown grouping columns: {missing}")

    names: list[str] = []
    seen: set[str] = set()
    for name in requested_columns:
        if name not in seen:
            seen.add(name)
            names.append(name)

    if requested_tags:
        tagged_names = [column.name for column in dataset.columns if dataset.column_has_any_tag(column, requested_tags)]
        if not tagged_names:
            raise KeyError(f"No grouping columns found for tags: {list(requested_tags)}")
        for name in tagged_names:
            if name not in seen:
                seen.add(name)
                names.append(name)

    return names


def _sample_frame(
    frame: pd.DataFrame,
    *,
    rows: int | None,
    frac: float | None,
    seed: int | None,
    replace: bool,
) -> pd.DataFrame:
    kwargs: dict[str, object] = {"replace": replace}
    if seed is not None:
        kwargs["random_state"] = int(seed)

    if rows is not None:
        if not replace and rows >= len(frame):
            return frame.copy()
        return frame.sample(n=rows, **kwargs)

    assert frac is not None
    return frame.sample(frac=frac, **kwargs)


def _comparator_op_tags(column: ColumnSpec) -> tuple[str, ...]:
    return merge_tags([tag for tag in column.tags if tag not in {"NUM", "<NUM>"}], ["CMP", "CAT"])


def _comparator_value_tags(column: ColumnSpec) -> tuple[str, ...]:
    preserved = [tag for tag in column.tags if tag != "<NUM>"]
    return merge_tags(preserved, ["NUM"])


def _item_column(dataset: TameDataset) -> ColumnSpec | None:
    return dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")


def _has_item_value(value: object) -> bool:
    if value is None:
        return False
    try:
        if bool(pd.isna(value)):
            return False
    except Exception:
        pass
    return str(value) != ""


def _reindex_columns(columns: list[ColumnSpec]) -> list[ColumnSpec]:
    normalized: list[ColumnSpec] = []
    for index, column in enumerate(columns):
        normalized.append(
            ColumnSpec(
                original_header=column.original_header,
                name=column.name,
                tags=column.tags,
            )
        )
    return normalized


def _format_threshold(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(float(value))


def _mapping_filename(column_name: str, used_names: set[str]) -> str:
    base = _safe_filename(column_name) or "column"
    candidate = f"{base}.mapping.csv"
    suffix = 1
    while candidate in used_names:
        candidate = f"{base}_{suffix}.mapping.csv"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _safe_filename(text: str) -> str:
    normalized = re.sub(r'[\\/:*?"<>|]+', "_", str(text).strip())
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = normalized.strip("._")
    return normalized


def _min_threshold(text: str) -> float:
    return min(float(part.strip()) for part in str(text).split(","))


def _max_threshold(text: str) -> float:
    return max(float(part.strip()) for part in str(text).split(","))
