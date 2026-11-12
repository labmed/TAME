"""Keep one source file, one TAME project and one result file in step.

    source (.xlsx/.csv/.tame, kept up to date by people)
        -> project .tame   settings + snapshot of the source rows + history
        -> result file     re-derived from the whole snapshot, new rows marked

A sync re-reads the source, reports the rows added, modified or removed since
the last snapshot, re-runs the declared WORK on the whole snapshot and reports
which result rows are new or changed. The three files keep their names; what
happened at each sync is recorded inside the project (LOG and SYNC_STATE).

Rules that keep this safe:

- Added rows are accepted. A modified or removed row that was already synced is
  a revision: nothing is written until it is accepted explicitly.
- All or nothing: if reading, checking or analysis fails, no file changes.
- The project is the commit point. The result is derived from it and can be
  regenerated; a missing or edited result file is detected and rebuilt.
"""
from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import math
import os
from html import escape
from pathlib import Path
import re
from typing import Any, Callable, Mapping

import pandas as pd

from .analysis_contract import resolve_id
from .cellstate import STATE_ABSENT, STATE_EMPTY, STATE_VALUE, STATE_WS, cell_state, parse_serialized_cell, token_config
from .config import ci_get
from .models import ColumnSpec, OperationOutput, TameDataset
from .tags import build_header, parse_header
from .toml_compat import dumps as dumps_toml
from .toml_compat import loads as loads_toml


SECTION = "SYNC"
STATE_SECTION = "SYNC_STATE"
RESULT_SECTION = "SYNC_RESULT"
RECORDS_SECTION = "SYNC_RECORDS"   # what people recorded about results (actions taken, reviews); never recomputed
MANAGED_SECTIONS = (STATE_SECTION, RECORDS_SECTION, "LOG", "PROVENANCE")  # written by the tool, never by hand
TRACK_COLUMNS = ("sync_seq", "synced_at", "sync_status")
NEW, CHANGED, SAME = "NEW", "CHANGED", "SAME"
STATE_VERSION = 1

_OPTIONS = {"SOURCE", "SHEET", "ENCODING", "KEY_IDS", "WORK", "RESULT", "RESULT_KEY", "REPORT", "ON_CHANGE"}

# Report writers by name. A plugin registers one and names it in its result's
# META[ANALYSIS_RESULT].REPORT; sync then writes META[SYNC].REPORT with it.
REPORT_RENDERERS: dict[str, Callable[[Mapping], str]] = {}
RESULT_NOTICES: dict[str, Callable[[Mapping], list]] = {}


def register_report(name: str, *, notices: Callable[[Mapping], list] | None = None):
    """Register ``renderer(context) -> html`` for results that declare this report name.

    ``context`` has: ``report`` (SyncReport), ``result`` (the annotated result dataset),
    ``history`` (one record per sync, the latest last), ``records`` (what people recorded
    in the project, oldest first), ``generated_at`` and ``tool_version``.

    ``notices(context) -> list[str]`` is optional: short reminders about the current
    result (``context`` has ``result`` and ``records``) that every sync prints.
    """
    def decorator(renderer):
        key = str(name).strip().upper()
        REPORT_RENDERERS[key] = renderer
        if notices is not None:
            RESULT_NOTICES[key] = notices
        return renderer
    return decorator


_DETAIL_LIMIT = 50  # row-level details kept in LOG per sync; counts are always complete
_BLANK = {STATE_ABSENT, STATE_EMPTY, STATE_WS}


class SyncError(ValueError):
    """A sync that cannot proceed. No file has been changed."""


@dataclass
class SyncConfig:
    source: Path
    source_label: str
    sheet: str | None
    key_ids: list[str]
    work: str | None
    result: Path | None
    result_label: str
    result_key: list[str]
    on_change: str
    report: Path | None = None
    report_label: str = ""
    encoding: str | None = None        # of a .csv/.tsv source that is not UTF-8 (META[SYNC].ENCODING)


@dataclass
class SourceTable:
    frame: pd.DataFrame        # template columns, canonical text cells
    rows: list[int]            # 1-based row number in the source for each frame row
    label: str
    sha256: str
    sheet: str = ""
    ignored_columns: list[str] = field(default_factory=list)


@dataclass
class DataDiff:
    mode: str                  # KEYED | POSITIONAL
    added: list[dict]
    modified: list[dict]
    removed: list[dict]
    frame: pd.DataFrame        # snapshot after the changes
    rows_before: int

    @property
    def changed(self) -> bool:
        return bool(self.added or self.modified or self.removed)


@dataclass
class SyncReport:
    project: str
    status: str = "PENDING"    # COMMITTED | UNCHANGED | BLOCKED | CHECKED
    sequence: int = 0          # sequence after this call
    timestamp: str = ""
    initial: bool = False
    dry_run: bool = False
    source: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    settings_changed: bool = False
    tool_changed: bool = False
    result: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    outputs: list[OperationOutput] = field(default_factory=list)
    new_rows: pd.DataFrame | None = None
    changed_rows: pd.DataFrame | None = None
    report_file: str = ""      # report written by this sync, if any
    notices: list[str] = field(default_factory=list)   # reminders from the result's plugin (e.g. rejections without an action record)

    def to_dict(self) -> dict:
        def records(frame):
            if frame is None:
                return []
            return [{key: (None if cell_state(value) != STATE_VALUE else value.item() if hasattr(value, "item") else value)
                     for key, value in row.items()} for row in frame.to_dict(orient="records")]
        return {"project": self.project, "status": self.status, "sequence": self.sequence, "timestamp": self.timestamp,
                "initial": self.initial, "dry_run": self.dry_run, "source": self.source, "data": self.data,
                "settings_changed": self.settings_changed, "tool_changed": self.tool_changed, "result": self.result,
                "report_file": self.report_file, "warnings": self.warnings, "reasons": self.reasons, "notices": self.notices,
                "outputs": [{"name": output.name, "message": output.message or "", "status": output.status,
                             "issues": len(output.issues or []), "warnings": list(output.warnings or [])} for output in self.outputs],
                "new_rows": records(self.new_rows), "changed_rows": records(self.changed_rows)}


# --------------------------------------------------------------------------- configuration

def sync_config(project: TameDataset, project_path: str | Path, *, source: str | None = None) -> SyncConfig:
    section = ci_get(project.meta, SECTION, None)
    if not isinstance(section, dict):
        raise SyncError("이 파일에는 META[SYNC] 설정이 없습니다. 예: [SYNC] SOURCE = \"qc_daily.xlsx\"")
    cfg = {str(key).upper(): value for key, value in section.items()}
    if set(cfg) - _OPTIONS:
        raise SyncError("알 수 없는 META[SYNC] 항목: " + ", ".join(sorted(set(cfg) - _OPTIONS)))
    base = Path(project_path).resolve().parent

    def text(key):
        value = cfg.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise SyncError(f"META[SYNC].{key} 는 비어 있지 않은 문자열이어야 합니다")
        return value.strip()

    def names(key):
        value = cfg.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value) or len(set(value)) != len(value):
            raise SyncError(f"META[SYNC].{key} 는 서로 다른 문자열 목록이어야 합니다")
        return list(value)

    source_label = source or text("SOURCE")
    if not source_label:
        raise SyncError("META[SYNC].SOURCE (원본 파일 경로)가 필요합니다")
    result_label = text("RESULT") or ""
    work = text("WORK")
    if result_label and not work:
        raise SyncError("META[SYNC].RESULT 를 쓰려면 실행할 META[SYNC].WORK 를 지정해야 합니다")
    if work and not isinstance(ci_get(ci_get(project.meta, "WORKS", {}), work, None), list):
        raise SyncError(f"META[SYNC].WORK = \"{work}\" 가 META[WORKS] 에 없습니다")
    on_change = str(cfg.get("ON_CHANGE", "STOP")).strip().upper()
    if on_change not in {"STOP", "ACCEPT"}:
        raise SyncError("META[SYNC].ON_CHANGE 는 STOP 또는 ACCEPT 여야 합니다")
    result = (base / result_label).resolve() if result_label else None
    if result is not None:
        if result.suffix.lower() not in {".tame", ".xlsx"} or result.name.lower().endswith((".meta.tame", ".data.tame")):
            raise SyncError("META[SYNC].RESULT 는 .tame 또는 .xlsx 파일이어야 합니다")
        if result in {Path(project_path).resolve(), (base / source_label).resolve()}:
            raise SyncError("결과 파일은 프로젝트·원본 파일과 달라야 합니다")
    report_label = text("REPORT") or ""
    report = (base / report_label).resolve() if report_label else None
    if report is not None:
        if result is None:
            raise SyncError("META[SYNC].REPORT 를 쓰려면 META[SYNC].RESULT 도 지정해야 합니다(보고서는 결과 파일의 내용으로 만듭니다)")
        if report.suffix.lower() not in {".html", ".htm"}:
            raise SyncError("META[SYNC].REPORT 는 .html 파일이어야 합니다")
        if report in {Path(project_path).resolve(), (base / source_label).resolve(), result}:
            raise SyncError("보고서 파일은 프로젝트·원본·결과 파일과 달라야 합니다")
    encoding = text("ENCODING")
    if encoding:
        import codecs
        try:
            codecs.lookup(encoding)
        except LookupError:
            raise SyncError(f"META[SYNC].ENCODING = \"{encoding}\" 는 알 수 없는 인코딩입니다 (예: cp949, utf-16)") from None
    return SyncConfig(source=(base / source_label).resolve(), source_label=source_label, sheet=text("SHEET"), encoding=encoding,
                      key_ids=names("KEY_IDS"), work=work, result=result, result_label=result_label,
                      result_key=names("RESULT_KEY"), on_change=on_change, report=report, report_label=report_label)


def sync_state(project: TameDataset) -> dict:
    state = ci_get(project.meta, STATE_SECTION, {})
    return deepcopy(state) if isinstance(state, dict) else {}


def sync_history(project: TameDataset) -> pd.DataFrame:
    """One row per committed sync, oldest first."""
    columns = ["SEQUENCE", "AT", "ROWS", "ADDED", "MODIFIED", "REMOVED", "SETTINGS_CHANGED",
               "RESULT_ROWS", "RESULT_NEW", "RESULT_CHANGED", "RESULT_REMOVED", "NOTE"]
    history = ci_get(sync_state(project), "HISTORY", [])
    return pd.DataFrame([{name: item.get(name, "") for name in columns} for item in history if isinstance(item, dict)], columns=columns)


# --------------------------------------------------------------------------- reading the source

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any, column: ColumnSpec, dataset: TameDataset) -> Any:
    """Spreadsheet cell -> the text that is stored in the project, without guessing.

    Typed date cells become ISO 8601; text cells are kept as typed. The same
    text is produced on every read, so stored and re-read rows compare equal.
    """
    if cell_state(value) != STATE_VALUE or isinstance(value, str):
        return value
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        stamp = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
        if stamp.tzinfo is None and stamp.time() == dt.time(0) and not dataset.column_has_tag(column, "DATETIME"):
            return stamp.date().isoformat()
        return stamp.isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def read_source(project: TameDataset, path: Path, *, sheet: str | None = None, label: str = "",
                encoding: str | None = None) -> SourceTable:
    """Read the source into the project's columns (matched by name; order is free)."""
    from . import io

    if not path.is_file():
        raise SyncError(f"원본 파일을 찾을 수 없습니다: {path}")
    suffix = path.suffix.lower()
    header_rows = io._header_rows(project.meta)
    cells = token_config(io._cell_settings(project.meta))
    used_sheet = ""
    try:
        if suffix in {".xlsx", ".xlsm"}:
            workbook = io.load_workbook_values(path)
            candidates = io._data_sheet_names(workbook)
            if sheet:
                if sheet not in workbook.sheetnames:
                    raise SyncError(f"원본에 시트 '{sheet}' 가 없습니다. 있는 시트: " + ", ".join(workbook.sheetnames))
                used_sheet = sheet
            elif len(candidates) == 1:
                used_sheet = candidates[0]
            else:
                raise SyncError("원본에 시트가 여러 개입니다. META[SYNC].SHEET 로 지정하세요: " + ", ".join(candidates))
            io.refuse_unevaluated_formulas(path, [used_sheet])
            rows = io._sheet_rows(workbook[used_sheet], cells)
        elif suffix in {".csv", ".tsv", ".txt"}:
            from io import StringIO
            from .text_encoding import decode_named, decode_utf8
            data = path.read_bytes()
            # UTF-8, or the encoding the project names in META[SYNC].ENCODING. Nothing is guessed.
            content = decode_named(data, encoding, path.name, keep_line_ends=True) if encoding else decode_utf8(
                data, path.name, keep_line_ends=True,
                hint='Save it as UTF-8, or name its encoding in the project file: [SYNC] ENCODING = "{encoding}"')
            rows = [[parse_serialized_cell(value, cells) for value in row]
                    for row in csv.reader(StringIO(content, newline=""), delimiter="," if suffix == ".csv" else "\t")]
        elif suffix == ".tame":
            other = io.read_tame(path)
            rows = [[column.name for column in other.columns], *other.df.values.tolist()]
            header_rows = 1
        else:
            raise SyncError(f"지원하지 않는 원본 형식입니다: {path.name} (.xlsx, .xlsm, .csv, .tsv, .tame)")
    except SyncError:
        raise
    except PermissionError as exc:
        raise SyncError(f"원본 파일을 읽을 수 없습니다(다른 프로그램이 잠갔을 수 있습니다): {path}") from exc
    except Exception as exc:
        raise SyncError(f"원본 파일을 읽지 못했습니다: {path.name}: {exc}") from exc

    headers, data_rows = io._split_header_rows(rows, header_rows)
    names = [parse_header(header)[0] for header in headers]
    positions: dict[str, int] = {}
    missing, repeated = [], []
    for column in project.columns:
        found = [index for index, name in enumerate(names) if name == column.name]
        if not found:
            missing.append(column.name)
        elif len(found) > 1:
            repeated.append(column.name)
        else:
            positions[column.name] = found[0]
    if missing or repeated:
        detail = []
        if missing:
            detail.append("없는 열: " + ", ".join(missing))
        if repeated:
            detail.append("중복된 열: " + ", ".join(repeated))
        raise SyncError("원본의 열 구성이 프로젝트와 다릅니다. " + "; ".join(detail)
                        + ". 원본의 열 이름: " + ", ".join(name for name in names if name))
    wanted = set(positions.values())
    ignored = [names[index] for index in range(len(names)) if index not in wanted
               and any(cell_state(row[index]) not in _BLANK for row in data_rows)]

    records, numbers = [], []
    for offset, row in enumerate(data_rows):
        values = [row[positions[column.name]] for column in project.columns]
        if all(cell_state(value) in _BLANK for value in values):
            continue  # formatted-but-empty spreadsheet rows
        records.append([_canonical(value, column, project) for value, column in zip(values, project.columns)])
        numbers.append(header_rows + offset + 1)
    frame = pd.DataFrame(records, columns=[column.name for column in project.columns], dtype=object)
    return SourceTable(frame=_as_stored(project, frame), rows=numbers, label=label or path.name,
                       sha256=_file_sha256(path), sheet=used_sheet, ignored_columns=ignored)


def _as_stored(project: TameDataset, frame: pd.DataFrame) -> pd.DataFrame:
    """The rows exactly as they will read back from the project file.

    Comparing in stored form means a value that the file format normalizes
    (for example a line break inside a cell) can never look like a change.
    """
    from . import io

    if frame.empty:
        return frame
    text = io._write_tsv(project.replace(df=frame, raw_sections={}), include_tags=True)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    stored = io._read_tsv(text, 1, io._cell_settings(project.meta))
    stored.columns = list(frame.columns)
    return stored


# --------------------------------------------------------------------------- comparing rows

def _cell_signature(value: Any, numeric: bool) -> tuple:
    from .provenance_audit import _cell
    return tuple(_cell(value, numeric))


def _shown(value: Any) -> str:
    state = cell_state(value)
    return str(value) if state == STATE_VALUE else f"<{state}>"


def _numeric_flags(dataset: TameDataset) -> list[bool]:
    return [dataset.column_has_any_tag(column, ("NUM", "<NUM>")) for column in dataset.columns]


def _cell_changes(old: list, new: list, names: list[str], numeric: list[bool]) -> list[dict]:
    return [{"column": name, "old": _shown(before), "new": _shown(after)}
            for before, after, name, flag in zip(old, new, names, numeric)
            if _cell_signature(before, flag) != _cell_signature(after, flag)]


def _number(value: Any) -> float | None:
    if cell_state(value) != STATE_VALUE:
        return None
    try:
        number = float(str(value))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _result_changes(old: list, new: list, names: list[str], numeric: list[bool]) -> list[dict]:
    """Like _cell_changes, but a computed number that only lost its last digits in a file is not a change.

    Excel keeps 15-16 significant digits, so a stored 0.3333333333333336 and a
    recomputed 0.33333333333333365 are the same result.
    """
    changes = []
    for item in _cell_changes(old, new, names, numeric):
        position = names.index(item["column"])
        before, after = _number(old[position]), _number(new[position])
        if numeric[position] and before is not None and after is not None and math.isclose(before, after, rel_tol=1e-12, abs_tol=1e-12):
            continue
        changes.append(item)
    return changes


def diff_rows(snapshot: pd.DataFrame, table: SourceTable, project: TameDataset, key_ids: list[str]) -> DataDiff:
    """Compare the stored snapshot with the source.

    With KEY_IDS, rows are matched by key and their order is free. Without
    them, the source is treated as append-only and rows are matched by position.
    """
    names = [column.name for column in project.columns]
    numeric = _numeric_flags(project)
    old_rows = snapshot[names].values.tolist()
    new_rows = table.frame[names].values.tolist()

    if not key_ids:
        shared = min(len(old_rows), len(new_rows))
        modified = [{"key": f"row {table.rows[index]}", "row": table.rows[index],
                     "changes": _cell_changes(old_rows[index], new_rows[index], names, numeric)}
                    for index in range(shared)]
        modified = [item for item in modified if item["changes"]]
        added = [{"key": f"row {table.rows[index]}", "row": table.rows[index]} for index in range(shared, len(new_rows))]
        removed = [{"key": f"row {index + 1} of the stored snapshot"} for index in range(shared, len(old_rows))]
        return DataDiff("POSITIONAL", added, modified, removed, table.frame.reset_index(drop=True), len(old_rows))

    key_positions = [names.index(resolve_id(project, identifier).name) for identifier in key_ids]

    def key_of(row, where):
        parts = []
        for position in key_positions:
            value = row[position]
            if cell_state(value) != STATE_VALUE or not str(value).strip():
                raise SyncError(f"{where}: 키 열 '{names[position]}' 값이 비어 있습니다 (META[SYNC].KEY_IDS)")
            parts.append(str(value).strip())
        return tuple(parts)

    def label(key):
        return " | ".join(f"{identifier}={part}" for identifier, part in zip(key_ids, key))

    old_index: dict[tuple, int] = {}
    for index, row in enumerate(old_rows):
        key = key_of(row, f"프로젝트 DATA {index + 1}행")
        if key in old_index:
            raise SyncError("프로젝트 DATA 에 같은 키가 중복되어 있습니다: " + label(key))
        old_index[key] = index
    new_index: dict[tuple, int] = {}
    for index, row in enumerate(new_rows):
        key = key_of(row, f"원본 {table.rows[index]}행")
        if key in new_index:
            raise SyncError(f"원본에 같은 키가 중복되어 있습니다: {label(key)} "
                            f"({table.rows[new_index[key]]}행, {table.rows[index]}행). "
                            "KEY_IDS 에 구분할 열을 추가하거나 원본을 정리하세요.")
        new_index[key] = index

    added, modified, removed, kept = [], [], [], []
    for key, index in old_index.items():
        if key not in new_index:
            removed.append({"key": label(key)})
            continue
        changes = _cell_changes(old_rows[index], new_rows[new_index[key]], names, numeric)
        if changes:
            modified.append({"key": label(key), "row": table.rows[new_index[key]], "changes": changes})
        kept.append(new_rows[new_index[key]])
    for key, index in new_index.items():
        if key not in old_index:
            added.append({"key": label(key), "row": table.rows[index]})
            kept.append(new_rows[index])
    # Stored order is the order of first appearance; re-sorting the source is not a change.
    return DataDiff("KEYED", added, modified, removed, pd.DataFrame(kept, columns=names, dtype=object), len(old_rows))


# --------------------------------------------------------------------------- result bookkeeping

def _result_key_columns(result: TameDataset, declared: list[str]) -> list[str]:
    names = [column.name for column in result.columns]
    keys = declared or ci_get(ci_get(result.meta, "ANALYSIS_RESULT", {}), "ROW_KEY", [])
    if not isinstance(keys, list) or any(key not in names for key in keys):
        raise SyncError("결과 행 키에 없는 열이 있습니다 (META[SYNC].RESULT_KEY 또는 플러그인의 ROW_KEY): " + ", ".join(map(str, keys)))
    return list(keys)


def annotate_result(result: TameDataset, previous: TameDataset | None, key_columns: list[str],
                    sequence: int, timestamp: str) -> tuple[TameDataset, dict, pd.DataFrame, pd.DataFrame]:
    """Mark each result row NEW, CHANGED or SAME against the previous result file.

    ``sync_seq``/``synced_at`` record the sync at which the row first appeared.
    Without key columns a row is identified by its whole content.
    """
    names = [column.name for column in result.columns]
    if set(names) & set(TRACK_COLUMNS):
        raise SyncError("결과에 sync 가 쓰는 열 이름이 이미 있습니다: " + ", ".join(sorted(set(names) & set(TRACK_COLUMNS))))
    numeric = _numeric_flags(result)
    rows = result.df[names].values.tolist()

    def identify(values_by_name, occurrences):
        if key_columns:
            return tuple(str(values_by_name[name]).strip() for name in key_columns)
        signature = tuple(("VALUE", format(_number(values_by_name.get(name)), ".12g"))
                          if flag and _number(values_by_name.get(name)) is not None
                          else _cell_signature(values_by_name.get(name), flag) for name, flag in zip(names, numeric))
        occurrences[signature] = occurrences.get(signature, 0) + 1
        return (signature, occurrences[signature])

    earlier: dict[tuple, dict] = {}
    duplicate_previous = False
    incomparable = previous is not None and not set(key_columns) <= {column.name for column in previous.columns}
    if incomparable:
        previous = None  # a result with other key columns cannot be matched row by row
    if previous is not None:
        seen: dict = {}
        for record in previous.df.to_dict(orient="records"):
            key = identify(record, seen)
            duplicate_previous = duplicate_previous or key in earlier
            earlier[key] = record
    statuses, sequences, stamps, changes = [], [], [], []
    seen = {}
    current_keys = set()
    for values in rows:
        record = dict(zip(names, values))
        key = identify(record, seen)
        if key in current_keys:
            raise SyncError("결과 행 키가 중복됩니다. META[SYNC].RESULT_KEY 를 확인하세요: " + ", ".join(map(str, key)))
        current_keys.add(key)
        before = earlier.get(key)
        if before is None:
            statuses.append(NEW)
            sequences.append(sequence)
            stamps.append(timestamp)
            continue
        difference = _result_changes([before.get(name) for name in names], values, names, numeric)
        statuses.append(CHANGED if difference else SAME)
        try:
            sequences.append(int(float(str(before.get(TRACK_COLUMNS[0])))))
        except (TypeError, ValueError):
            sequences.append(sequence)
        stamp = before.get(TRACK_COLUMNS[1])
        stamps.append(str(stamp) if cell_state(stamp) == STATE_VALUE else timestamp)
        if difference:
            changes.append({"key": " | ".join(f"{name}={record[name]}" for name in key_columns) or "(row)", "changes": difference})
    removed = [" | ".join(map(str, key)) if key_columns else "(row)" for key in earlier if key not in current_keys]

    frame = result.df.copy()
    frame[TRACK_COLUMNS[0]] = sequences
    frame[TRACK_COLUMNS[1]] = stamps
    frame[TRACK_COLUMNS[2]] = statuses
    tags = {TRACK_COLUMNS[0]: ("NUM",), TRACK_COLUMNS[1]: ("STR",), TRACK_COLUMNS[2]: ("STR",)}
    columns = [*result.columns, *[ColumnSpec(build_header(name, tags[name]), name, tags[name]) for name in TRACK_COLUMNS]]
    meta = deepcopy(result.meta)
    section = next((key for key in meta if str(key).upper() == "COLUMN"), "COLUMN")
    meta[section] = {**(meta.get(section) or {}), **{name: {"ID": name} for name in TRACK_COLUMNS}}
    annotated = TameDataset(frame, columns, meta, result.schema, result.job, {}, result.source_path)
    summary = {"rows": len(frame), "new": statuses.count(NEW), "changed": statuses.count(CHANGED),
               "same": statuses.count(SAME), "removed": len(removed), "key": list(key_columns),
               "changed_detail": changes[:_DETAIL_LIMIT], "removed_detail": removed[:_DETAIL_LIMIT]}
    if duplicate_previous:
        summary["note"] = "이전 결과 파일에 같은 키의 행이 여러 개 있었습니다."
    if incomparable:
        summary["note"] = "이전 결과 파일에 결과 행 키 열이 없어 비교하지 못했습니다. 모든 행을 NEW 로 표시했습니다."
    status = pd.Series(statuses, index=frame.index, dtype=object)
    return annotated, summary, frame.loc[status.eq(NEW)].copy(), frame.loc[status.eq(CHANGED)].copy()


def read_result(path: str | Path) -> TameDataset:
    """The result file of a sync project (``.tame`` or ``.xlsx``) as a dataset."""
    from . import io
    path = Path(path)
    return io.read_xlsx(path) if path.suffix.lower() == ".xlsx" else io.read_tame(path)


_read_result = read_result


def _write_result(path: Path, dataset: TameDataset) -> None:
    from . import io
    if path.suffix.lower() == ".xlsx":
        io.write_xlsx(path, dataset, standardize_sex_values=False)
    else:
        io.write_tame(path, dataset, standardize_sex_values=False)


# --------------------------------------------------------------------------- writing the project

_TABLE_HEADER = re.compile(r"""^\s*\[\[?\s*(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_-]+))[^\[\]]*\]\]?\s*(?:#.*)?$""")


def splice_managed_meta(original: str, meta: dict) -> tuple[str, bool]:
    """META text for ``meta`` that keeps the hand-written part of ``original`` verbatim.

    Only the tool-managed tables (SYNC_STATE, LOG, PROVENANCE) are regenerated,
    after the hand-written settings, so comments and layout survive every sync.
    Returns the text and whether the original text could be preserved. If the
    spliced text would not parse back to exactly ``meta``, plain serialization
    is used instead.
    """
    managed_names = {name.upper() for name in MANAGED_SECTIONS}
    managed = {name: value for name in MANAGED_SECTIONS
               for key, value in meta.items() if str(key).upper() == name}
    kept, keep = [], True
    for line in original.splitlines():
        match = _TABLE_HEADER.match(line)
        if match:
            root = next(group for group in match.groups() if group is not None)
            keep = root.upper() not in managed_names
        if keep:
            kept.append(line)
    text = "\n".join(kept).rstrip()
    tail = dumps_toml(managed).rstrip()
    candidate = (text + "\n\n" + tail if text and tail else text or tail) + "\n"
    try:
        if loads_toml(candidate) == meta:
            return candidate, True
    except Exception:
        pass
    return dumps_toml(meta), False


def project_text(project_path: Path, dataset: TameDataset) -> tuple[str, bool]:
    """Full project file text: hand-written sections and the DATA header are kept as written."""
    from . import io

    raw = io._read_tame_sections(project_path)
    meta_text, preserved = splice_managed_meta(raw.get("META", ""), dataset.meta)
    header_rows = max(io._header_rows(dataset.meta), 0)
    header_lines = raw.get("DATA", "").splitlines()[:header_rows]
    if len(header_lines) != header_rows or not any(line.strip() for line in header_lines):
        header_lines = ["\t".join(dataset.tagged_headers())] if header_rows else []
    data_text = io._write_tsv(dataset, include_tags=True)
    body = data_text.split("\n", 1)[1] if "\n" in data_text else ""
    data = "\n".join([*header_lines, body]) if body else "\n".join(header_lines)
    sections = [(name, raw[name].rstrip()) for name in ("SCHEMA", "JOB") if raw.get(name, "").strip()]
    sections.append(("META", meta_text.rstrip()))
    sections.append(("DATA", data))
    return io.tame_file_text(sections), preserved


def _check_writable(path: Path) -> None:
    if not path.exists():
        return
    try:
        with path.open("r+b"):
            pass
    except PermissionError as exc:
        raise SyncError(f"파일을 쓸 수 없습니다(다른 프로그램에서 열려 있으면 닫아 주세요): {path}") from exc


def _temporary(path: Path) -> Path:
    return path.with_name(f".{path.stem}.sync-tmp{path.suffix}")


# --------------------------------------------------------------------------- the sync itself

def sync_project(project_path: str | Path, *, source: str | None = None, accept_changes: bool = False,
                 dry_run: bool = False, allow_plugins: bool = False, allow_functions: bool = False,
                 now: str | None = None) -> SyncReport:
    """Bring the project and its result up to date with the source.

    ``dry_run`` reports what a sync would do and writes nothing.
    ``accept_changes`` records modified/removed source rows as accepted revisions.
    """
    from . import io
    from .pipeline import execute_work
    from .provenance import append_log_entry, execution_environment, operation_scope
    from .provenance_audit import artifact_descriptor, data_fingerprint

    path = Path(project_path)
    if path.suffix.lower() != ".tame" or path.name.lower().endswith((".meta.tame", ".data.tame")):
        raise SyncError("sync 프로젝트는 META 와 DATA 를 함께 가진 .tame 파일이어야 합니다")
    project = io.read_tame(path)
    config = sync_config(project, path, source=source)
    state = sync_state(project)
    sequence = int(state.get("SEQUENCE", 0) or 0)
    initial = sequence == 0
    timestamp = now or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    report = SyncReport(project=str(path), sequence=sequence, timestamp=timestamp, initial=initial, dry_run=dry_run)

    table = read_source(project, config.source, sheet=config.sheet, label=config.source_label, encoding=config.encoding)
    snapshot = project.df.iloc[0:0] if initial else project.df
    diff = diff_rows(snapshot, table, project, config.key_ids)
    report.source = {"path": config.source_label, "sheet": table.sheet, "rows": len(table.frame), "sha256": table.sha256,
                     "file_changed": bool(state.get("SOURCE_SHA256")) and state.get("SOURCE_SHA256") != table.sha256}
    report.data = {"mode": diff.mode, "key_ids": list(config.key_ids), "rows_before": diff.rows_before,
                   "rows_after": len(diff.frame), "added": diff.added, "modified": diff.modified, "removed": diff.removed}
    if table.ignored_columns:
        report.warnings.append("프로젝트에 정의되지 않은 원본 열은 읽지 않았습니다: " + ", ".join(table.ignored_columns))
    if initial and len(project.df):
        report.warnings.append(f"첫 동기화입니다. 프로젝트에 있던 DATA {len(project.df)}행은 원본 내용으로 대체됩니다.")

    recorded = artifact_descriptor(project)
    data_edited = not initial and bool(state.get("DATA_SHA256")) and state.get("DATA_SHA256") != recorded["DATA_SHA256"]
    settings_changed = not initial and state.get("CONTROL_SHA256") != recorded["CONTROL_SHA256"]
    tool_hash = execution_environment()["IMPLEMENTATION_SHA256"]
    # The tool hash covers the tametools package. Plugins the WORK uses from outside it (a plugin folder, a pip
    # package, a module named in META) are tracked by their own hashes, so a changed plugin is noticed as well.
    # "Uses" is what the last sync really ran (a plugin may run another one without declaring it) together with
    # what the WORK names now.
    recorded_plugins = state.get("PLUGIN_SHA256") if isinstance(state.get("PLUGIN_SHA256"), dict) else {}
    plugin_hashes = _work_plugin_sources(project.meta, config.work, allow_plugins, also=recorded_plugins) if config.work else {}
    plugins_changed = [] if initial else sorted(name for name in {*plugin_hashes, *recorded_plugins}
                                                if plugin_hashes.get(name) != recorded_plugins.get(name))
    core_changed = not initial and bool(config.work) and state.get("TOOL_SHA256") != tool_hash
    tool_changed = core_changed or bool(plugins_changed)
    report.settings_changed, report.tool_changed = settings_changed, tool_changed
    if data_edited:
        report.warnings.append("프로젝트의 DATA 가 마지막 동기화 이후 sync 밖에서 수정되었습니다. 아래 비교는 수정된 내용을 기준으로 합니다.")

    previous_result, result_state = None, "NONE"
    if config.result is not None:
        result_state = "MISSING"
        if config.result.exists():
            try:
                previous_result = _read_result(config.result)
                result_state = "OK" if state.get("RESULT_SHA256") == data_fingerprint(previous_result) else "MODIFIED"
            except Exception as exc:  # an unreadable result is rebuilt, never trusted
                result_state = "UNREADABLE"
                report.warnings.append(f"기존 결과 파일을 읽을 수 없어 새로 만듭니다: {exc}")
        if not initial and result_state == "MISSING":
            report.warnings.append("결과 파일이 없어 전체를 다시 만듭니다. 모든 결과 행이 NEW 로 표시됩니다.")
        if not initial and result_state == "MODIFIED":
            report.warnings.append("결과 파일이 마지막 동기화 이후 바뀌었습니다(직접 수정했거나 이전 저장이 끝나지 않음). 다시 만듭니다.")
        if initial and previous_result is not None:
            report.warnings.append("결과 파일이 이미 있습니다. 그 내용과 비교해 NEW/CHANGED 를 표시합니다.")
    report.result = {"path": config.result_label, "state": result_state}
    records = sync_records(project)
    broken = verify_records(records)
    if broken:
        report.warnings.append("프로젝트에 남긴 기록 중 도구 밖에서 수정되었거나 빠진 것이 있습니다: #"
                               + ", #".join(str(item) for item in broken))
    if previous_result is not None and result_state == "OK":
        _load_plugins(project.meta, allow_plugins)
        report.notices = result_notices(previous_result, records)

    if data_edited:
        report.reasons.append("프로젝트 DATA 의 직접 수정을 이력에 기록합니다")
    if settings_changed:
        report.reasons.append("프로젝트 설정(META)이 마지막 동기화 이후 바뀌었습니다")
    if core_changed:
        report.reasons.append("tametools 구현이 마지막 동기화 이후 바뀌었습니다(분석을 다시 확인합니다)")
    if plugins_changed:
        report.reasons.append("플러그인 구현이 마지막 동기화 이후 바뀌었습니다: " + ", ".join(plugins_changed) + " (분석을 다시 확인합니다)")
    if config.result is not None and not initial and result_state != "OK":
        report.reasons.append("결과 파일을 다시 만들어야 합니다")
    if config.report is not None and not initial and not config.report.exists():
        report.reasons.append("보고서 파일을 다시 만들어야 합니다")

    revisions = bool(diff.modified or diff.removed) and not initial
    accepted = accept_changes or config.on_change == "ACCEPT"
    if revisions and not accepted:
        report.status = "BLOCKED"
        return report
    if not (initial or diff.changed or settings_changed or tool_changed or report.reasons):
        report.status = "UNCHANGED"
        return report
    if not dry_run:
        for target in (path, config.result, config.report):
            if target is not None:
                _check_writable(target)

    new_sequence = sequence + 1
    parameters = {"SEQUENCE": new_sequence, "INITIAL": initial, "MODE": diff.mode, "KEY_IDS": list(config.key_ids),
                  "SOURCE": {"NAME": config.source_label, "SHEET": table.sheet, "SHA256": table.sha256, "ROWS": len(table.frame),
                             **({"ENCODING": config.encoding} if config.encoding else {})},
                  "ACCEPTED_REVISIONS": bool(revisions), "SETTINGS_CHANGED": settings_changed, "TOOL_CHANGED": tool_changed,
                  "ADDED": [item["key"] for item in diff.added[:_DETAIL_LIMIT]],
                  "MODIFIED": deepcopy(diff.modified[:_DETAIL_LIMIT]), "REMOVED": [item["key"] for item in diff.removed[:_DETAIL_LIMIT]],
                  "DETAIL_TRUNCATED": max(len(diff.added), len(diff.modified), len(diff.removed)) > _DETAIL_LIMIT}
    counts = {"ADDED": len(diff.added), "MODIFIED": len(diff.modified), "REMOVED": len(diff.removed)}

    with operation_scope("SYNC", [project], parameters={"SEQUENCE": new_sequence, "SOURCE": config.source_label}):
        base = project
        if settings_changed or data_edited:
            # The edit itself happened outside the tool; record that it was found and what it changed.
            previous_control, previous_data = state.get("CONTROL_SHA256", ""), state.get("DATA_SHA256", "")
            base = append_log_entry(project, action="MANUAL_EDIT", inputs=[],
                message="동기화 시점에 프로젝트 파일의 직접 편집을 확인했습니다. 편집 전후 지문을 기록했습니다.",
                parameters={"changed": [name for name, flag in (("CONTROL_SHA256", settings_changed), ("DATA_SHA256", data_edited)) if flag],
                            "before": {"CONTROL_SHA256": previous_control, "DATA_SHA256": previous_data},
                            "after": {"CONTROL_SHA256": recorded["CONTROL_SHA256"], "DATA_SHA256": recorded["DATA_SHA256"]},
                            "recording": "Detected by sync; the edit itself was made outside tametools."})
        staged = base.replace(df=diff.frame.reset_index(drop=True), raw_sections={})
        message = (f"원본 {config.source_label} 동기화 #{new_sequence}: 추가 {len(diff.added)}, 수정 {len(diff.modified)}, "
                   f"삭제 {len(diff.removed)} (전체 {len(diff.frame)}행)")
        logged = append_log_entry(staged, action="SYNC", input_dataset=base, parameters=parameters, counts=counts,
                                  message=message, warnings=list(report.warnings))

        annotated, summary = None, {}
        if config.work:
            from .plugin_base.manager import called_plugins
            with called_plugins() as called:
                pipeline = execute_work(logged, config.work, allow_functions=allow_functions,
                                        allow_plugins=allow_plugins, log_level="detailed")
            # What is kept for the next sync to compare: the code of every plugin this run really used.
            plugin_hashes = _work_plugin_sources(project.meta, config.work, allow_plugins, also=called)
            report.outputs = list(pipeline.outputs)
            failed = [output.name for output in pipeline.outputs if output.status != "SUCCEEDED"]
            if failed:
                raise SyncError("분석 단계가 완료되지 않아 아무것도 저장하지 않았습니다: " + ", ".join(failed))
            if config.result is not None:
                final = pipeline.final_dataset
                keys = _result_key_columns(final, config.result_key)
                annotated, summary, report.new_rows, report.changed_rows = annotate_result(
                    final, previous_result, keys, new_sequence, timestamp)
                marked = deepcopy(annotated.meta)
                marked[RESULT_SECTION] = {"PROJECT": path.name, "SOURCE": config.source_label, "WORK": config.work,
                                          "SEQUENCE": new_sequence, "SYNCED_AT": timestamp}
                details = {"SEQUENCE": new_sequence, "ROW_KEY": keys, "CHANGED": summary["changed_detail"],
                           "REMOVED": summary["removed_detail"]}
                with operation_scope("SYNC:RESULT", [final], parameters=details):
                    annotated = append_log_entry(annotated.replace(meta=marked), action="SYNC:RESULT", input_dataset=final,
                        parameters=details,
                        counts={"NEW": summary["new"], "CHANGED": summary["changed"], "SAME": summary["same"], "REMOVED": summary["removed"]},
                        message=f"결과 표시 #{new_sequence}: 새 결과 {summary['new']}, 변경 {summary['changed']}, 삭제 {summary['removed']}, 유지 {summary['same']}")
                report.result.update({name: summary[name] for name in ("rows", "new", "changed", "same", "removed", "key", "changed_detail", "removed_detail")})
                if summary.get("note"):
                    report.warnings.append(summary["note"])
                summary_columns = ci_get(ci_get(final.meta, "ANALYSIS_RESULT", {}), "SUMMARY_COLUMNS", [])
                report.result["summary_columns"] = [name for name in summary_columns if isinstance(name, str)]

        report.sequence = new_sequence
        if dry_run:
            report.status = "CHECKED"
            return report

        # Commit: the result is prepared first, the project is replaced first.
        result_hash, result_tmp, report_tmp, project_tmp = "", None, None, _temporary(path)

        def discard():
            for leftover in (project_tmp, result_tmp, report_tmp):
                if leftover is not None and leftover.exists():
                    leftover.unlink()

        try:
            if annotated is not None:
                result_tmp = _temporary(config.result)
                _write_result(result_tmp, annotated)
                result_hash = data_fingerprint(_read_result(result_tmp))
        except BaseException:
            discard()
            raise
        history = [item for item in state.get("HISTORY", []) if isinstance(item, dict)]
        note = "; ".join(report.reasons)
        history.append({"SEQUENCE": new_sequence, "AT": timestamp, "SOURCE_SHA256": table.sha256, "ROWS": len(diff.frame),
                        "ADDED": len(diff.added), "MODIFIED": len(diff.modified), "REMOVED": len(diff.removed),
                        "SETTINGS_CHANGED": settings_changed, "RESULT_ROWS": summary.get("rows", 0),
                        "RESULT_NEW": summary.get("new", 0), "RESULT_CHANGED": summary.get("changed", 0),
                        "RESULT_REMOVED": summary.get("removed", 0), "NOTE": note})
        after = artifact_descriptor(logged)
        new_state = {"VERSION": STATE_VERSION, "SEQUENCE": new_sequence, "SYNCED_AT": timestamp,
                     "SOURCE": config.source_label, "SOURCE_SHA256": table.sha256, "ROWS": len(diff.frame),
                     "DATA_SHA256": after["DATA_SHA256"], "CONTROL_SHA256": after["CONTROL_SHA256"],
                     "TOOL_SHA256": tool_hash, "RESULT": config.result_label, "RESULT_ROWS": summary.get("rows", 0),
                     "RESULT_SHA256": result_hash, "REPORT": config.report_label, "HISTORY": history}
        if plugin_hashes:
            new_state["PLUGIN_SHA256"] = plugin_hashes
        final_meta = {key: value for key, value in logged.meta.items() if str(key).upper() != STATE_SECTION}
        final_meta[STATE_SECTION] = new_state
        try:
            if annotated is not None:
                report.notices = result_notices(annotated, records)
            if config.report is not None and annotated is not None:
                page = _render_report(report, annotated, history, records, timestamp)
                report_tmp = _temporary(config.report)
                report_tmp.write_text(page, encoding="utf-8")
            text, preserved = project_text(path, logged.replace(meta=final_meta))
            if not preserved:
                report.warnings.append("META 의 직접 작성한 부분을 그대로 보존하지 못해 표준 형식으로 다시 썼습니다(주석이 사라질 수 있습니다).")
            project_tmp.write_text(text, encoding="utf-8")
            os.replace(project_tmp, path)
        except BaseException:
            discard()
            raise
        if result_tmp is not None:
            try:
                os.replace(result_tmp, config.result)
            except OSError as exc:
                result_tmp.unlink(missing_ok=True)
                report.warnings.append(f"프로젝트는 갱신했지만 결과 파일을 바꾸지 못했습니다({exc}). 파일을 닫고 sync 를 다시 실행하면 결과만 다시 만듭니다.")
                report.result["state"] = "NOT_WRITTEN"
        if report_tmp is not None:
            try:
                os.replace(report_tmp, config.report)
                report.report_file = config.report_label
            except OSError as exc:
                report_tmp.unlink(missing_ok=True)
                report.warnings.append(f"보고서 파일을 바꾸지 못했습니다({exc}). 파일을 닫고 보고서를 지운 뒤 sync 를 다시 실행하세요.")
        report.status = "COMMITTED"
    return report


# --------------------------------------------------------------------------- records kept in the project

_RECORD_HASHES = {"SHA256", "PREVIOUS_SHA256"}


def sync_records(project: TameDataset) -> list[dict]:
    """What people recorded about the results (oldest first). Results are recomputed; these are not."""
    section = ci_get(project.meta, RECORDS_SECTION, {})
    items = ci_get(section, "ITEMS", []) if isinstance(section, dict) else []
    return [deepcopy(item) for item in items if isinstance(item, dict)]


def _record_hash(record: Mapping, previous: str) -> str:
    from .provenance import digest
    return digest({"PREVIOUS": previous, "RECORD": {key: value for key, value in record.items() if key not in _RECORD_HASHES}})


def verify_records(records: list[dict]) -> list:
    """IDs of records that no longer match their hash chain (edited or removed outside the tool)."""
    broken, previous = [], ""
    for number, record in enumerate(records, 1):
        if record.get("ID") != number or record.get("PREVIOUS_SHA256", "") != previous or record.get("SHA256") != _record_hash(record, previous):
            broken.append(record.get("ID", number))
        previous = str(record.get("SHA256", ""))
    return broken


def recordable(project_path: str | Path, kind: str, supersedes: int = 0):
    """The project and its records, once it is established that a record of ``kind`` may be added now.

    Callers that collect a record from a person check this first, so nobody fills in a form that cannot be saved.
    """
    from . import io
    from .provenance_audit import artifact_descriptor

    path = Path(project_path)
    project = io.read_tame(path)
    sync_config(project, path)
    state = sync_state(project)
    if not int(state.get("SEQUENCE", 0) or 0):
        raise SyncError("아직 동기화한 적이 없는 프로젝트입니다. 먼저 tametools sync 를 실행하세요")
    current = artifact_descriptor(project)
    if state.get("DATA_SHA256") != current["DATA_SHA256"] or state.get("CONTROL_SHA256") != current["CONTROL_SHA256"]:
        raise SyncError("프로젝트의 설정 또는 DATA 가 마지막 동기화 이후 직접 수정되었습니다. "
                        "먼저 tametools sync 를 실행해 그 변경을 기록한 뒤 다시 시도하세요")
    kind = str(kind).strip().upper()
    records = sync_records(project)
    if supersedes:
        target = next((record for record in records if record.get("ID") == supersedes), None)
        if target is None or target.get("KIND") != kind:
            raise SyncError(f"정정할 기록 #{supersedes} 이(가) 없거나 종류가 다릅니다")
        if any(record.get("SUPERSEDES") == supersedes for record in records):
            raise SyncError(f"기록 #{supersedes} 은(는) 이미 다른 기록으로 정정되었습니다. 가장 최근 기록을 정정하세요")
    _check_writable(path)
    return project, records


def add_record(project_path: str | Path, kind: str, fields: Mapping, *, by: str, supersedes: int = 0,
               now: str | None = None) -> dict:
    """Append one record to the project. Earlier records are never edited: a correction is a new record.

    The record is chained to the previous one by hash and written to the execution LOG as well.
    """
    from .provenance import append_log_entry, operation_scope

    path = Path(project_path)
    kind = str(kind).strip().upper()
    if not kind or not isinstance(by, str) or not by.strip():
        raise SyncError("기록에는 종류와 기록자(--by)가 필요합니다")
    project, records = recordable(path, kind, supersedes)
    for key, value in fields.items():
        valid = isinstance(value, (str, int)) and not isinstance(value, bool) or (
            isinstance(value, list) and all(isinstance(item, str) for item in value))
        if not valid or str(key).upper() in {"ID", "KIND", "AT", "BY", "SUPERSEDES", *_RECORD_HASHES}:
            raise SyncError(f"기록 항목 {key} 의 값이 올바르지 않습니다")
    previous = str(records[-1].get("SHA256", "")) if records else ""
    record = {"ID": len(records) + 1, "KIND": kind, "AT": now or dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "BY": by.strip(), **{str(key).upper(): value for key, value in fields.items()}, "SUPERSEDES": int(supersedes or 0)}
    record["PREVIOUS_SHA256"] = previous
    record["SHA256"] = _record_hash(record, previous)
    meta = {key: value for key, value in project.meta.items() if str(key).upper() != RECORDS_SECTION}
    meta[RECORDS_SECTION] = {"ITEMS": [*records, record]}
    _check_writable(path)
    with operation_scope("SYNC:RECORD", [project], parameters={"ID": record["ID"], "KIND": kind}):
        logged = append_log_entry(project.replace(meta=meta, raw_sections={}), action="SYNC:RECORD", input_dataset=project,
                                  parameters=record, counts={"RECORDS": len(records) + 1},
                                  message=f"{kind} 기록 #{record['ID']} ({record['BY']})"
                                          + (f", #{supersedes} 정정" if supersedes else ""))
    text, _ = project_text(path, logged)
    temporary = _temporary(path)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return record


def _report_name(result: TameDataset | None) -> str:
    if result is None:
        return ""
    return str(ci_get(ci_get(result.meta, "ANALYSIS_RESULT", {}), "REPORT", "") or "").strip().upper()


def _work_plugin_sources(meta: dict, work: str, allow_external: bool, also=()) -> dict[str, str]:
    """Hash of every plugin the WORK runs whose code is not part of the tametools package (name -> SHA-256).

    ``also`` names plugins beyond the WORK's own steps: the ones a run was seen to use.
    """
    from .pipeline import _resolve_work
    from .plugin_base.manager import plugin_sources

    try:
        steps = _resolve_work(meta, work)
    except KeyError:
        return {}       # an undefined WORK is reported where it is run
    return plugin_sources([*steps, *sorted(str(name) for name in also)], meta, allow_external=allow_external)


def _load_plugins(meta: dict, allow_external: bool = False) -> None:
    """Report writers and notices are registered when plugin files are loaded."""
    from .plugin_base.manager import list_plugins
    list_plugins(meta, allow_external=allow_external)


def result_notices(result: TameDataset | None, records: list[dict]) -> list[str]:
    hook = RESULT_NOTICES.get(_report_name(result))
    return [str(line) for line in hook({"result": result, "records": records})] if hook else []


def _render_report(report: "SyncReport", result: TameDataset, history: list, records: list[dict], generated_at: str) -> str:
    from .provenance import execution_environment
    renderer = REPORT_RENDERERS.get(_report_name(result), plain_report)
    return renderer({"report": report, "result": result, "history": history, "records": records,
                     "generated_at": generated_at, "tool_version": execution_environment()["TOOL_VERSION"]})


def last_sync_report(project_path: Path, project: TameDataset, result: TameDataset) -> "SyncReport":
    """What the last committed sync did, rebuilt from the project and result files."""
    from .provenance import log_entries, restore_log_values

    state = sync_state(project)
    history = [item for item in state.get("HISTORY", []) if isinstance(item, dict)]
    latest = history[-1] if history else {}
    entry = next((item for item in reversed(log_entries(project)) if item.get("OPERATION") == "SYNC"), {})
    params = restore_log_values(entry.get("PARAMS", {})) or {}
    marked = next((item for item in reversed(log_entries(result)) if item.get("OPERATION") == "SYNC:RESULT"), {})
    details = restore_log_values(marked.get("PARAMS", {})) or {}
    status = result.df[TRACK_COLUMNS[2]].map(str) if TRACK_COLUMNS[2] in result.df.columns else pd.Series("", index=result.df.index)
    rows_after = int(state.get("ROWS", 0) or 0)
    report = SyncReport(project=str(project_path), status="COMMITTED", sequence=int(state.get("SEQUENCE", 0) or 0),
                        timestamp=str(state.get("SYNCED_AT", "")), initial=int(state.get("SEQUENCE", 0) or 0) == 1)
    report.source = {"path": state.get("SOURCE", ""), "sheet": ci_get(params.get("SOURCE", {}), "SHEET", ""),
                     "rows": rows_after, "sha256": state.get("SOURCE_SHA256", ""), "file_changed": False}
    report.data = {"mode": params.get("MODE", ""), "key_ids": list(params.get("KEY_IDS", []) or []),
                   "rows_before": rows_after - int(latest.get("ADDED", 0) or 0) + int(latest.get("REMOVED", 0) or 0),
                   "rows_after": rows_after,
                   "added": [{"key": key, "row": 0} for key in params.get("ADDED", []) or []],
                   "modified": list(params.get("MODIFIED", []) or []),
                   "removed": [{"key": key} for key in params.get("REMOVED", []) or []],
                   "added_count": int(latest.get("ADDED", 0) or 0), "modified_count": int(latest.get("MODIFIED", 0) or 0),
                   "removed_count": int(latest.get("REMOVED", 0) or 0)}
    report.result = {"path": state.get("RESULT", ""), "state": "OK", "rows": len(result.df),
                     "new": int(status.eq(NEW).sum()), "changed": int(status.eq(CHANGED).sum()), "same": int(status.eq(SAME).sum()),
                     "removed": int(latest.get("RESULT_REMOVED", 0) or 0),
                     "key": list(ci_get(ci_get(result.meta, "ANALYSIS_RESULT", {}), "ROW_KEY", []) or []),
                     "changed_detail": list(details.get("CHANGED", []) or []), "removed_detail": list(details.get("REMOVED", []) or [])}
    report.warnings = [str(item) for item in entry.get("WARNINGS", []) or []]
    report.reasons = [part for part in str(latest.get("NOTE", "")).split("; ") if part]
    report.settings_changed = bool(latest.get("SETTINGS_CHANGED", False))
    report.new_rows, report.changed_rows = result.df.loc[status.eq(NEW)].copy(), result.df.loc[status.eq(CHANGED)].copy()
    return report


def rerender_report(project_path: str | Path, *, now: str | None = None, allow_plugins: bool = False) -> str:
    """Write META[SYNC].REPORT again from the files as they are: no source is read, nothing is recomputed.

    Used after a record is added. Returns WRITTEN, NONE (no report configured) or
    STALE (the result file is missing or not the one of the last sync; the next sync rebuilds both).
    """
    from . import io
    from .provenance_audit import data_fingerprint

    path = Path(project_path)
    project = io.read_tame(path)
    config = sync_config(project, path)
    state = sync_state(project)
    if config.report is None or config.result is None:
        return "NONE"
    if not config.result.exists():
        return "STALE"
    try:
        result = _read_result(config.result)
    except Exception:
        return "STALE"
    if state.get("RESULT_SHA256") != data_fingerprint(result):
        return "STALE"
    _load_plugins(project.meta, allow_plugins)
    report = last_sync_report(path, project, result)
    page = _render_report(report, result, [item for item in state.get("HISTORY", []) if isinstance(item, dict)],
                          sync_records(project), now or dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    _check_writable(config.report)
    temporary = _temporary(config.report)
    try:
        temporary.write_text(page, encoding="utf-8")
        os.replace(temporary, config.report)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return "WRITTEN"


# --------------------------------------------------------------------------- report text

def plain_report(context: Mapping) -> str:
    """Fallback HTML report for results whose plugin has no report of its own: what changed, as tables."""
    report, result = context["report"], context["result"]
    parts = ["<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>" + escape(Path(report.project).name) + "</title>",
             "<style>body{font:14px/1.6 system-ui,sans-serif;max-width:1080px;margin:28px auto;padding:0 20px}"
             "table{border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid #ddd;padding:5px 8px;text-align:left}"
             "pre{white-space:pre-wrap;background:#f6f6f4;padding:12px}</style></head><body>",
             "<h1>" + escape(Path(report.project).name) + f" · 동기화 #{report.sequence}</h1>",
             "<pre>" + escape(format_report(report, max_rows=0)) + "</pre>"]
    shown = [name for name in result.df.columns if name not in TRACK_COLUMNS]
    for label, frame in (("새 결과", report.new_rows), ("변경된 기존 결과", report.changed_rows)):
        if frame is not None and not frame.empty:
            parts.append(f"<h2>{label} ({len(frame)}행)</h2>" + frame[shown].head(500).to_html(index=False, escape=True, na_rep="—"))
    parts.append("<p>이 결과의 플러그인은 차트가 있는 전용 보고서를 제공하지 않습니다.</p></body></html>")
    return "".join(parts)


def format_report(report: SyncReport, *, max_rows: int = 30) -> str:
    lines = []
    title = {"COMMITTED": "동기화 완료", "UNCHANGED": "변경 없음", "BLOCKED": "중단: 확인이 필요한 수정/삭제",
             "CHECKED": "점검만 수행(저장하지 않음)"}.get(report.status, report.status)
    lines.append(f"[SYNC] {Path(report.project).name}  #{report.sequence}  {title}")
    lines.append(f"  시각: {report.timestamp}")
    source = report.source
    sheet = f" [{source['sheet']}]" if source.get("sheet") else ""
    lines.append(f"  원본: {source.get('path', '')}{sheet}  {source.get('rows', 0)}행  sha256 {str(source.get('sha256', ''))[:12]}")
    data = report.data
    head = "첫 동기화" if report.initial else "원본 변경"
    lines.append(f"  {head}: 추가 {len(data['added'])} · 수정 {len(data['modified'])} · 삭제 {len(data['removed'])}"
                 f"  (저장된 {data['rows_before']}행 → {data['rows_after']}행, "
                 + ("키: " + ", ".join(data["key_ids"]) if data["key_ids"] else "행 위치 기준") + ")")
    if not report.initial:
        for item in data["added"][:max_rows]:
            lines.append(f"    + {item['key']}  (원본 {item['row']}행)")
        if len(data["added"]) > max_rows:
            lines.append(f"    + … 외 {len(data['added']) - max_rows}행")
    for item in data["modified"][:max_rows]:
        lines.append(f"    ~ {item['key']}  (원본 {item['row']}행)")
        for change in item["changes"]:
            lines.append(f"        {change['column']}: {change['old']} → {change['new']}")
    if len(data["modified"]) > max_rows:
        lines.append(f"    ~ … 외 {len(data['modified']) - max_rows}행")
    for item in data["removed"][:max_rows]:
        lines.append(f"    - {item['key']}")
    if len(data["removed"]) > max_rows:
        lines.append(f"    - … 외 {len(data['removed']) - max_rows}행")
    for reason in report.reasons:
        lines.append(f"  확인: {reason}")
    for output in report.outputs:
        issues = len(output.issues or [])
        lines.append(f"  단계 {output.name}: {output.message or output.status}" + (f"  (검증 이슈 {issues}건)" if issues else ""))
    result = report.result
    if result.get("path") and "rows" in result:
        lines.append(f"  결과: {result['path']}  {result['rows']}행  "
                     f"새 결과 {result['new']} · 변경 {result['changed']} · 삭제 {result['removed']} · 유지 {result['same']}")
        shown = [*result.get("key", []), *[name for name in result.get("summary_columns", []) if name not in result.get("key", [])]]
        for label, frame in (("새 결과", report.new_rows), ("변경된 기존 결과", report.changed_rows)):
            if frame is None or frame.empty:
                continue
            if label == "새 결과" and len(frame) == result["rows"] and len(frame) > max_rows:
                lines.append(f"  새 결과: 전체 {len(frame)}행이 새로 만들어졌습니다(목록 생략). 내용은 결과 파일에서 확인하세요.")
                continue
            columns = [name for name in shown if name in frame.columns] or [name for name in frame.columns if name not in TRACK_COLUMNS][:10]
            lines.append(f"  {label} ({len(frame)}행" + (f", 처음 {max_rows}행만 표시" if len(frame) > max_rows else "") + ")")
            lines.extend("    " + line for line in frame[columns].head(max_rows).to_string(index=False).splitlines())
        for item in result.get("changed_detail", [])[:max_rows]:
            lines.append(f"    ~ {item['key']}")
            for change in item["changes"]:
                lines.append(f"        {change['column']}: {change['old']} → {change['new']}")
        for key in result.get("removed_detail", [])[:max_rows]:
            lines.append(f"    - 사라진 결과: {key}")
    elif result.get("path"):
        lines.append(f"  결과: {result['path']}  (상태 {result.get('state', '')})")
    if report.report_file:
        lines.append(f"  보고서: {report.report_file}  (차트 포함, 브라우저로 열기)")
    for notice in report.notices:
        lines.append(f"  알림: {notice}")
    for warning in report.warnings:
        lines.append(f"  주의: {warning}")
    if report.status == "BLOCKED":
        lines.append("  이미 동기화한 행이 원본에서 수정되거나 삭제되었습니다. 아무것도 저장하지 않았습니다.")
        lines.append("  의도한 정정이면 --accept-changes 로 다시 실행하세요(수정 내용이 이력에 기록됩니다).")
    if report.status == "CHECKED":
        lines.append("  --check: 파일은 바뀌지 않았습니다.")
    return "\n".join(lines)
