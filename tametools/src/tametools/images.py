from __future__ import annotations

from base64 import b64decode, b64encode
import mimetypes
from pathlib import Path
import re
from typing import Any, Iterable

import pandas as pd

from .cellstate import STATE_VALUE, cell_state
from .models import ColumnSpec, TameDataset
from .tags import build_header, merge_tags


DATA_URL_RE = re.compile(r"^data:(?P<mime>[-\w.+/]+);base64,(?P<payload>[A-Za-z0-9+/=\s]+)$")
MIME_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def extract_image_columns(
    dataset: TameDataset,
    output_dir: str | Path,
    *,
    target_columns: Iterable[str] | None = None,
    path_style: str = "relative",
) -> tuple[TameDataset, pd.DataFrame]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    target_names = set(target_columns or ())
    selected = [
        column
        for column in dataset.columns
        if column.has_tag("IMAGE") and column.has_tag("B64") and (not target_names or column.name in target_names)
    ]

    frame = dataset.df.copy()
    manifest_rows: list[dict[str, Any]] = []
    updated_columns: list[ColumnSpec] = []
    for column in dataset.columns:
        if column not in selected:
            updated_columns.append(column)
            continue

        column_dir = output_root / column.name
        column_dir.mkdir(parents=True, exist_ok=True)
        for row_index, value in frame[column.name].items():
            if cell_state(value) != STATE_VALUE:
                continue
            mime, payload = decode_image_value(str(value))
            ext = MIME_EXTENSION.get(mime, mimetypes.guess_extension(mime) or ".bin")
            filename = f"row_{int(row_index) + 1:06d}{ext}"
            file_path = column_dir / filename
            file_path.write_bytes(payload)
            stored = str(file_path if path_style == "absolute" else file_path)
            frame.at[row_index, column.name] = stored
            manifest_rows.append(
                {
                    "row_index": int(row_index),
                    "column": column.name,
                    "path": stored,
                    "mime": mime,
                    "bytes": len(payload),
                }
            )

        updated_columns.append(_replace_column_tag(column, old_tag="B64", new_tag="PATH"))

    manifest = pd.DataFrame(manifest_rows, columns=["row_index", "column", "path", "mime", "bytes"])
    return dataset.replace(df=frame, columns=_reindex_columns(updated_columns)), manifest


def embed_image_columns(
    dataset: TameDataset,
    *,
    base_dir: str | Path | None = None,
    target_columns: Iterable[str] | None = None,
) -> tuple[TameDataset, pd.DataFrame]:
    root = Path(base_dir) if base_dir is not None else Path.cwd()
    target_names = set(target_columns or ())
    selected = [
        column
        for column in dataset.columns
        if column.has_tag("IMAGE") and column.has_tag("PATH") and (not target_names or column.name in target_names)
    ]

    frame = dataset.df.copy()
    manifest_rows: list[dict[str, Any]] = []
    updated_columns: list[ColumnSpec] = []
    for column in dataset.columns:
        if column not in selected:
            updated_columns.append(column)
            continue

        for row_index, value in frame[column.name].items():
            if cell_state(value) != STATE_VALUE:
                continue
            file_path = Path(str(value))
            resolved = file_path if file_path.is_absolute() else root / file_path
            payload = resolved.read_bytes()
            mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            frame.at[row_index, column.name] = encode_image_value(payload, mime=mime)
            manifest_rows.append(
                {
                    "row_index": int(row_index),
                    "column": column.name,
                    "path": str(value),
                    "resolved_path": str(resolved),
                    "mime": mime,
                    "bytes": len(payload),
                }
            )

        updated_columns.append(_replace_column_tag(column, old_tag="PATH", new_tag="B64"))

    manifest = pd.DataFrame(
        manifest_rows,
        columns=["row_index", "column", "path", "resolved_path", "mime", "bytes"],
    )
    return dataset.replace(df=frame, columns=_reindex_columns(updated_columns)), manifest


def is_valid_image_value(value: Any, *, mode: str) -> bool:
    if cell_state(value) != STATE_VALUE:
        return False
    text = str(value)
    if mode == "B64":
        try:
            decode_image_value(text)
            return True
        except ValueError:
            return False
    if mode == "PATH":
        return bool(text.strip())
    return False


def decode_image_value(text: str) -> tuple[str, bytes]:
    match = DATA_URL_RE.match(text.strip())
    if match:
        try:
            return match.group("mime"), b64decode(match.group("payload"))
        except Exception as exc:
            raise ValueError(f"Invalid image base64 payload: {exc}") from exc
    try:
        return "application/octet-stream", b64decode(text.strip())
    except Exception as exc:
        raise ValueError(f"Invalid image base64 payload: {exc}") from exc


def encode_image_value(payload: bytes, *, mime: str) -> str:
    return f"data:{mime};base64,{b64encode(payload).decode('ascii')}"


def _replace_column_tag(column: ColumnSpec, *, old_tag: str, new_tag: str) -> ColumnSpec:
    tags = merge_tags([tag for tag in column.tags if tag != old_tag], [new_tag])
    return ColumnSpec(
        index=column.index,
        original_header=build_header(column.name, tags),
        name=column.name,
        tags=tags,
    )


def _reindex_columns(columns: list[ColumnSpec]) -> list[ColumnSpec]:
    normalized: list[ColumnSpec] = []
    for index, column in enumerate(columns):
        normalized.append(
            ColumnSpec(
                index=index,
                original_header=column.original_header,
                name=column.name,
                tags=column.tags,
            )
        )
    return normalized
