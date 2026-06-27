from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

import pandas as pd

from .cellstate import serialize_cell
from .metadata import format_settings
from .models import TameDataset
from .sex import standardize_sex_dataset
from .toml_compat import dumps as dumps_toml


FORMAT_ALIASES = {
    "csv": "csv",
    "tsv": "tsv",
    "jsonl": "jsonl",
    "ndjson": "jsonl",
    "sql": "sql",
    "parquet": "parquet",
    "feather": "feather",
    "bundle": "r_bundle",
    "r_bundle": "r_bundle",
    "r-bundle": "r_bundle",
}
SUFFIX_FORMATS = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".sql": "sql",
    ".parquet": "parquet",
    ".feather": "feather",
    ".bundle": "r_bundle",
    ".rbundle": "r_bundle",
}
TEXT_EXPORT_FORMATS = {"csv", "tsv", "jsonl"}
BUNDLE_DATA_FORMATS = {"csv", "tsv", "jsonl", "parquet", "feather"}


@dataclass(frozen=True)
class ExportResult:
    format: str
    output_path: str
    files: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def available_export_formats() -> tuple[str, ...]:
    return ("csv", "tsv", "jsonl", "sql", "parquet", "feather", "r_bundle")


def export_dataset(
    dataset: TameDataset,
    output: str | Path,
    *,
    format: str | None = None,
    bundle_data_format: str = "csv",
    include_schema: bool = True,
    include_job: bool = True,
) -> ExportResult:
    normalized = normalize_export_format(format or infer_export_format(output))
    output_path = Path(output)
    if normalized == "r_bundle":
        return export_r_bundle(
            dataset,
            output_path,
            data_format=bundle_data_format,
            include_schema=include_schema,
            include_job=include_job,
        )
    if normalized in TEXT_EXPORT_FORMATS:
        return _export_text(dataset, output_path, format=normalized)
    if normalized == "sql":
        return _export_sql(dataset, output_path)
    if normalized == "parquet":
        return _export_parquet(dataset, output_path)
    if normalized == "feather":
        return _export_feather(dataset, output_path)
    raise ValueError(f"Unsupported export format: {format}")


def export_r_bundle(
    dataset: TameDataset,
    output_dir: str | Path,
    *,
    data_format: str = "csv",
    include_schema: bool = True,
    include_job: bool = True,
) -> ExportResult:
    bundle_format = normalize_bundle_data_format(data_format)
    bundle_dir = Path(output_dir)
    warnings: list[str] = []
    if bundle_dir.exists() and not bundle_dir.is_dir():
        raise ValueError(f"Bundle output path must be a directory: {bundle_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=True)

    data_result = export_dataset(dataset, bundle_dir / f"data.{_data_extension(bundle_format)}", format=bundle_format)
    files = [Path(path) for path in data_result.files]

    columns_path = bundle_dir / "columns.csv"
    _write_delimited(_bundle_columns_frame(dataset), columns_path, delimiter=",")
    files.append(columns_path)

    meta_path = bundle_dir / "meta.toml"
    meta_path.write_text(dumps_toml(dataset.meta), encoding="utf-8")
    files.append(meta_path)

    if include_schema and (dataset.raw_sections.get("SCHEMA") or dataset.schema):
        schema_path = bundle_dir / "schema.toml"
        schema_path.write_text(dataset.raw_sections.get("SCHEMA") or dumps_toml(dataset.schema), encoding="utf-8")
        files.append(schema_path)

    if include_job and (dataset.raw_sections.get("JOB") or dataset.job):
        job_path = bundle_dir / "job.toml"
        job_path.write_text(dataset.raw_sections.get("JOB") or dumps_toml(dataset.job), encoding="utf-8")
        files.append(job_path)

    manifest_path = bundle_dir / "bundle.json"
    manifest_path.write_text(
        json.dumps(_bundle_manifest(dataset, bundle_format), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files.append(manifest_path)

    readme_path = bundle_dir / "README_R.md"
    readme_path.write_text(_bundle_readme(dataset, bundle_format), encoding="utf-8")
    files.append(readme_path)

    loader_path = bundle_dir / "load_tame_bundle.R"
    loader_path.write_text(_r_loader_script(), encoding="utf-8")
    files.append(loader_path)

    warnings.extend(data_result.warnings)
    return ExportResult(
        format="r_bundle",
        output_path=str(bundle_dir),
        files=tuple(str(path) for path in files),
        warnings=tuple(warnings),
    )


def infer_export_format(output: str | Path) -> str:
    suffix = Path(output).suffix.lower()
    if suffix in SUFFIX_FORMATS:
        return SUFFIX_FORMATS[suffix]
    if suffix == "":
        return "r_bundle"
    raise ValueError(f"Cannot infer export format from output path: {output}")


def normalize_export_format(value: str) -> str:
    normalized = FORMAT_ALIASES.get(str(value).strip().lower())
    if normalized is None:
        raise ValueError(f"Unsupported export format: {value}")
    return normalized


def normalize_bundle_data_format(value: str) -> str:
    normalized = normalize_export_format(value)
    if normalized not in BUNDLE_DATA_FORMATS:
        raise ValueError(f"Unsupported r_bundle data format: {value}")
    return normalized


def _export_text(dataset: TameDataset, output_path: Path, *, format: str) -> ExportResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = _serialized_export_frame(dataset)
    if format == "csv":
        _write_delimited(frame, output_path, delimiter=",")
    elif format == "tsv":
        _write_delimited(frame, output_path, delimiter="\t")
    elif format == "jsonl":
        _write_jsonl(frame, output_path)
    else:
        raise ValueError(f"Unsupported text export format: {format}")
    return ExportResult(format=format, output_path=str(output_path), files=(str(output_path),))


def _export_parquet(dataset: TameDataset, output_path: Path) -> ExportResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = _serialized_export_frame(dataset).astype("string")
    try:
        frame.to_parquet(output_path, index=False)
    except ImportError as exc:
        raise ImportError("Parquet export requires pyarrow or fastparquet.") from exc
    return ExportResult(format="parquet", output_path=str(output_path), files=(str(output_path),))


def _export_feather(dataset: TameDataset, output_path: Path) -> ExportResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = _serialized_export_frame(dataset).astype("string")
    try:
        frame.to_feather(output_path)
    except ImportError as exc:
        raise ImportError("Feather export requires pyarrow.") from exc
    return ExportResult(format="feather", output_path=str(output_path), files=(str(output_path),))


def _export_sql(dataset: TameDataset, output_path: Path) -> ExportResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_sql_script(dataset, base_name=_sql_base_name(output_path)), encoding="utf-8")
    return ExportResult(format="sql", output_path=str(output_path), files=(str(output_path),))


def _serialized_export_frame(dataset: TameDataset) -> pd.DataFrame:
    dataset = standardize_sex_dataset(dataset)
    settings = format_settings(dataset.meta)
    exported = pd.DataFrame(index=dataset.df.index)
    for column in dataset.columns:
        exported[column.name] = dataset.df[column.name].map(lambda value: str(serialize_cell(value, settings, for_excel=False)))
    return exported


def _bundle_columns_frame(dataset: TameDataset) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "index": list(range(len(dataset.columns))),
            "name": [column.name for column in dataset.columns],
            "original_header": [column.original_header for column in dataset.columns],
            "tagged_header": [column.tagged_header for column in dataset.columns],
            "tags": ["::".join(column.tags) for column in dataset.columns],
            "primary_tag": [column.tags[0] if column.tags else "" for column in dataset.columns],
        }
    )


def _sql_script(dataset: TameDataset, *, base_name: str) -> str:
    data_table = _quote_sql_identifier(f"{base_name}_data")
    columns_table = _quote_sql_identifier(f"{base_name}_columns")
    sections_table = _quote_sql_identifier(f"{base_name}_sections")
    exported = _serialized_export_frame(dataset)

    lines = [
        "-- TAME SQL export",
        f"-- source_path: {dataset.source_path or ''}",
        "BEGIN TRANSACTION;",
        f'CREATE TABLE {data_table} ("row_index" INTEGER PRIMARY KEY',
    ]
    for column in dataset.columns:
        lines[-1] += f', {_quote_sql_identifier(column.name)} TEXT'
    lines[-1] += ");"

    lines.append(
        f'CREATE TABLE {columns_table} ("index" INTEGER NOT NULL, "name" TEXT NOT NULL, "original_header" TEXT NOT NULL, "tagged_header" TEXT NOT NULL, "tags" TEXT NOT NULL, "primary_tag" TEXT NOT NULL);'
    )
    lines.append(f'CREATE TABLE {sections_table} ("section" TEXT PRIMARY KEY, "content" TEXT NOT NULL);')

    for row in _bundle_columns_frame(dataset).itertuples(index=False):
        values = [
            str(row.index),
            _quote_sql_literal(str(row.name)),
            _quote_sql_literal(str(row.original_header)),
            _quote_sql_literal(str(row.tagged_header)),
            _quote_sql_literal(str(row.tags)),
            _quote_sql_literal(str(row.primary_tag)),
        ]
        lines.append(f"INSERT INTO {columns_table} VALUES ({', '.join(values)});")

    section_names = ("META", "SCHEMA", "JOB")
    for section in section_names:
        text = dataset.raw_sections.get(section)
        if text is None:
            if section == "META" and dataset.meta:
                text = dumps_toml(dataset.meta)
            elif section == "SCHEMA" and dataset.schema:
                text = dumps_toml(dataset.schema)
            elif section == "JOB" and dataset.job:
                text = dumps_toml(dataset.job)
        if text:
            lines.append(
                f"INSERT INTO {sections_table} VALUES ({_quote_sql_literal(section)}, {_quote_sql_literal(text)});"
            )

    column_sql = ", ".join([_quote_sql_identifier("row_index")] + [_quote_sql_identifier(column.name) for column in dataset.columns])
    for row_index, row in enumerate(exported.itertuples(index=False), start=1):
        values = [str(row_index)] + [_quote_sql_literal("" if value is None else str(value)) for value in row]
        lines.append(f"INSERT INTO {data_table} ({column_sql}) VALUES ({', '.join(values)});")

    lines.append("COMMIT;")
    lines.append("")
    return "\n".join(lines)


def _sql_base_name(path: Path) -> str:
    stem = path.stem or "tame"
    base = re.sub(r"[^0-9A-Za-z_]+", "_", stem).strip("_").lower() or "tame"
    if base[0].isdigit():
        base = f"t_{base}"
    return base


def _quote_sql_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _quote_sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _bundle_manifest(dataset: TameDataset, bundle_format: str) -> dict[str, object]:
    return {
        "format": "r_bundle",
        "data_format": bundle_format,
        "data_file": f"data.{_data_extension(bundle_format)}",
        "rows": len(dataset.df),
        "columns": len(dataset.columns),
        "source_path": dataset.source_path or "",
        "sections": sorted(dataset.raw_sections) or ["META", "DATA"],
        "tokens": dataset.settings(),
    }


def _bundle_readme(dataset: TameDataset, bundle_format: str) -> str:
    return (
        "# TAME R Bundle\n\n"
        "이 폴더는 `tametools export ... --format r_bundle` 결과다.\n\n"
        "구성:\n"
        f"- `data.{_data_extension(bundle_format)}`: 직렬화된 데이터 본문\n"
        "- `columns.csv`: 컬럼 이름, 태그, 원본 헤더\n"
        "- `meta.toml`: META 섹션\n"
        "- `schema.toml`, `job.toml`: 있으면 함께 저장\n"
        "- `load_tame_bundle.R`: R 로더 예제\n\n"
        "권장:\n"
        "- R에서 컬럼명을 그대로 유지하려면 `check.names = FALSE`를 사용한다.\n"
        "- 값 상태는 `<<ABSENT>>`, `<<NULL>>`, `<<EMPTY>>`, `<<WS:n>>` 토큰으로 저장된다.\n"
        "- `RESULT::<NUM>` 같은 comparator 컬럼은 문자형으로 읽은 뒤 처리하거나, export 전에 `split-comparator`를 실행한다.\n"
        "- 순수 수치 분석이 목적이면 `NUM` 컬럼 위주의 데이터셋은 `parquet` 또는 `feather` bundle도 적합하다.\n\n"
        "예시:\n"
        "```r\n"
        "source(\"load_tame_bundle.R\")\n"
        "bundle <- read_tame_bundle(\".\")\n"
        "str(bundle$data)\n"
        "subset(bundle$columns, grepl(\"RESULT\", tags, fixed = TRUE))\n"
        "```\n"
    )


def _r_loader_script() -> str:
    return """read_tame_bundle <- function(path = \".\") {
  path <- normalizePath(path, mustWork = TRUE)
  data_file <- .tametools_find_data_file(path)
  columns_path <- file.path(path, \"columns.csv\")
  if (!file.exists(columns_path)) {
    stop(\"columns.csv not found in bundle: \", path)
  }

  list(
    data = .tametools_read_data(data_file),
    columns = utils::read.csv(
      columns_path,
      stringsAsFactors = FALSE,
      check.names = FALSE,
      na.strings = character()
    ),
    meta_text = .tametools_read_optional_text(file.path(path, \"meta.toml\")),
    schema_text = .tametools_read_optional_text(file.path(path, \"schema.toml\")),
    job_text = .tametools_read_optional_text(file.path(path, \"job.toml\"))
  )
}

.tametools_find_data_file <- function(path) {
  candidates <- c(\"data.parquet\", \"data.feather\", \"data.csv\", \"data.tsv\", \"data.jsonl\")
  matches <- file.path(path, candidates[file.exists(file.path(path, candidates))])
  if (length(matches) == 0) {
    stop(\"No data.* file found in bundle: \", path)
  }
  matches[[1]]
}

.tametools_read_data <- function(path) {
  if (grepl(\"\\\\.parquet$\", path, ignore.case = TRUE)) {
    if (!requireNamespace(\"arrow\", quietly = TRUE)) {
      stop(\"Reading parquet bundles requires the R package 'arrow'.\")
    }
    return(as.data.frame(arrow::read_parquet(path)))
  }
  if (grepl(\"\\\\.feather$\", path, ignore.case = TRUE)) {
    if (!requireNamespace(\"arrow\", quietly = TRUE)) {
      stop(\"Reading feather bundles requires the R package 'arrow'.\")
    }
    return(as.data.frame(arrow::read_feather(path)))
  }
  if (grepl(\"\\\\.jsonl$\", path, ignore.case = TRUE)) {
    if (!requireNamespace(\"jsonlite\", quietly = TRUE)) {
      stop(\"Reading jsonl bundles requires the R package 'jsonlite'.\")
    }
    return(jsonlite::stream_in(file(path), verbose = FALSE))
  }
  if (grepl(\"\\\\.tsv$\", path, ignore.case = TRUE)) {
    return(utils::read.delim(path, stringsAsFactors = FALSE, check.names = FALSE, na.strings = character()))
  }
  utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE, na.strings = character())
}

.tametools_read_optional_text <- function(path) {
  if (!file.exists(path)) {
    return(\"\")
  }
  paste(readLines(path, warn = FALSE, encoding = \"UTF-8\"), collapse = \"\\n\")
}
"""


def _write_delimited(frame: pd.DataFrame, output_path: Path, *, delimiter: str) -> None:
    frame.to_csv(output_path, sep=delimiter, index=False, encoding="utf-8")


def _write_jsonl(frame: pd.DataFrame, output_path: Path) -> None:
    frame.to_json(output_path, orient="records", lines=True, force_ascii=False)


def _data_extension(bundle_format: str) -> str:
    return "jsonl" if bundle_format == "jsonl" else bundle_format
