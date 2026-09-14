"""아무 xlsx/csv 입력에서 시작용 .tame 를 자동 생성한다(태그·데이터타입 추정).

처음 사용자의 진입장벽을 낮추기 위해, 평범한 표를 받아 컬럼명과 **전체 값**을 근거로 가장 좁은
데이터 타입을 추정한 시작용 TAME 를 만든다. 다중 시트 xlsx 는 시트를 선택할 수 있고, 어떤 파일의
어떤 시트에서 생성되었는지 META.INFO 와 LOG(provenance)에 기록한다. 추정 데이터타입은 META 에도
기록한다. 부등호 허용 여부가 다른 ``<NUM>`` 과 ``NUM`` 은 반드시 구분한다.
"""
from __future__ import annotations

import re
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pandas as pd

__all__ = ["list_sheets", "infer_narrow_type", "infer_column_tags", "init_from_table"]

_STRICT_NUM = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
_COMPARATOR = re.compile(r"^[<>]=?\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
_STATE_TOKEN = re.compile(r"^<<.*>>$")
_DATE_LIKE = re.compile(r"(?:\d{1,4}[-/.]\d{1,2}(?:[-/.]\d{1,4})?|\d{1,2}:\d{2})")
_LEADING_ZERO = re.compile(r"^0\d+$")

_ID = re.compile(r"(등록번호|검체번호|환자번호|\bid\b|accession|mrn|sid|pid|순번|번호)", re.IGNORECASE)
_SEX = re.compile(r"(성별|\bsex\b|\bgender\b)", re.IGNORECASE)
_AGE = re.compile(r"(나이|연령|\bage\b)", re.IGNORECASE)
_TEST = re.compile(r"(검사항목|검사명|검사|항목|test[_ ]?name|\btest\b|item|analyte)", re.IGNORECASE)
_HOSP = re.compile(r"(기관|병원|hospital|\bsite\b|institution|lab[_ ]?id)", re.IGNORECASE)
_NAME = re.compile(r"(성명|이름|환자명|patient[_ ]?name|\bname\b)", re.IGNORECASE)
_DATE_NAME = re.compile(r"(일시|일자|날짜|시각|date|time|collected|reported)", re.IGNORECASE)


def list_sheets(path: str | Path) -> list[str] | None:
    """xlsx 면 시트명 목록, 아니면 None."""
    p = Path(path)
    if p.suffix.lower() not in (".xlsx", ".xls"):
        return None
    with pd.ExcelFile(p) as workbook:
        return list(workbook.sheet_names)


def _clean(series: pd.Series) -> pd.Series:
    s = series.dropna().astype(str).str.strip()
    s = s[(s != "") & (~s.str.match(_STATE_TOKEN))]
    return s


def infer_narrow_type(series: pd.Series) -> str:
    """전체 값을 읽어 가장 좁은 데이터타입을 추정한다.

    반환: ``<NUM>`` | ``NUM`` | ``DATETIME`` | ``DATE`` | ``CATEGORY`` | ``STR``.
    - 부등호(<,>) 가 하나라도 있고 나머지가 수치/부등호이면 ``<NUM>`` (NUM 과 반드시 구분).
    - 모든 값이 엄격 수치이면 ``NUM``.
    - 날짜형이면 시간 성분 유무로 ``DATETIME``/``DATE``.
    - 낮은 카디널리티의 비수치이면 ``CATEGORY``, 그 외 ``STR``.
    """
    s = _clean(series)
    if len(s) == 0:
        return "STR"
    is_cmp = s.str.match(_COMPARATOR)
    is_num = s.str.match(_STRICT_NUM)
    if is_cmp.any() and bool((is_cmp | is_num).all()):
        return "<NUM>"
    if bool(is_num.all()):
        # 앞자리 0 이 있는 숫자(등록번호/검체번호 등)는 숫자로 보면 정보가 손상되므로 STR 로 둔다.
        if bool(s.str.match(_LEADING_ZERO).any()):
            return "STR"
        return "NUM"
    # 날짜형: 값이 날짜처럼 보일 때만 파싱(숫자를 날짜로 오인하지 않게)
    if bool((s.str.contains(_DATE_LIKE)).mean() >= 0.95):
        parsed = pd.to_datetime(s, errors="coerce")
        if float(parsed.notna().mean()) >= 0.95:
            has_time = bool(((parsed.dt.hour != 0) | (parsed.dt.minute != 0) | (parsed.dt.second != 0)).any())
            return "DATETIME" if has_time else "DATE"
    nunique = int(s.nunique())
    if nunique <= 12 and nunique < len(s):
        return "CATEGORY"
    return "STR"


def infer_column_tags(name: str, series: pd.Series) -> tuple[str, ...]:
    """컬럼명과 전체 값으로 헤더 태그(의미+타입)를 추정한다."""
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ")
    if name.lower() in {"seqn", "sqn"}:
        return ("ID", "STR")
    if name.lower() in {"source", "source dataset", "source id", "dataset"}:
        return ("SOURCE", "CATEGORY", "STR")
    if _TEST.fullmatch(name):
        return ("TESTNAME",)
    if _NAME.search(name):
        return ("NAME", "STR")
    if _ID.search(name) or name.endswith(("ID", "Id", "_id", "_ID")):  # PatientID/SampleID 등 camelCase
        return ("ID", "STR")
    if _HOSP.search(name):
        return ("HOSPITAL_ID", "STR")
    if _SEX.search(name):
        return ("SEX",)
    if _AGE.search(name):
        if re.search(r"\bage\s*(?:5|10|group|band|category)\b|연령군|나이군", name, re.IGNORECASE):
            return ("BY", "AGE_GROUP", "CATEGORY")
        return ("AGE",)
    if _TEST.search(name):
        return ("TESTNAME",)
    dtype = infer_narrow_type(series)
    if _DATE_NAME.search(name) and dtype not in ("DATE", "DATETIME"):
        dtype = "DATETIME"
    if dtype in ("DATE", "DATETIME"):
        return (dtype,)
    if dtype == "<NUM>":
        return ("RESULT", "<NUM>")
    if dtype == "NUM":
        return ("RESULT", "NUM")
    if dtype == "CATEGORY":
        return ("CATEGORY",)
    return ("STR",)


def _read_table(path: Path, sheet: str | None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=sheet if sheet is not None else 0, dtype=str, keep_default_na=False)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _toml_quote(key: str) -> str:
    return '"' + str(key).replace("\\", "\\\\").replace('"', '\\"') + '"'


def init_from_table(input_path: str | Path, output_path: str | Path, *, sheet: str | None = None,
                    definitions_path: str | Path | None = None, interactive: bool = False,
                    input_fn=None, output_fn=None) -> dict:
    """Infer a starter, expose unresolved interpretation, and accept reviewed TOML definitions."""
    from .io import write_tame
    from .models import TameDataset, ColumnSpec
    from .cellstate import parse_serialized_cell
    from .provenance import append_log_entry
    from .init_review import load_definitions, review_dataset, template_text
    from .init_semantics import semantic_profiles, configure_interactively, apply_options, validate_options
    from .toml_compat import dumps

    p, out = Path(input_path), Path(output_path)
    df = _read_table(p, sheet)
    definitions_file = Path(definitions_path) if definitions_path is not None else None
    definitions = load_definitions(definitions_file)
    configs = definitions.get("COLUMN", {})
    if any(not isinstance(config, dict) for config in configs.values()):
        raise ValueError("Each COLUMN definition must be a TOML table")
    if set(configs) - set(df.columns):
        raise ValueError(f"Definitions reference absent columns: {sorted(set(configs) - set(df.columns))}")
    specs, col_info, physical_types = [], [], {}
    for name in df.columns:
        tags = configs.get(name, {}).get("TAGS", infer_column_tags(str(name), df[name]))
        if not isinstance(tags, (list, tuple)) or not tags or any(not isinstance(t, str) for t in tags):
            raise ValueError(f"COLUMN.{name}.TAGS must be a nonempty list of tags")
        from .tags import TAG_TOKEN_RE
        if any(not TAG_TOKEN_RE.fullmatch(t) for t in tags):
            raise ValueError(f"COLUMN.{name}.TAGS contains an invalid tag")
        dtype = infer_narrow_type(df[name]);physical_types[str(name)] = dtype
        spec = ColumnSpec(str(name), str(name), tuple(tags));specs.append(spec)
        col_info.append((str(name), "::".join(spec.tags), dtype))
    # Use the actual TAME serializer for tabs/newlines and explicit state tokens.
    frame = df.apply(lambda s: s.map(lambda v: parse_serialized_cell(str(v)) if v != "" else None))
    source_sheet = sheet if sheet is not None else (list_sheets(p) or [None])[0]
    meta = deepcopy(definitions)
    meta["INFO"] = {"DESCRIPTION": "starter TAME; review unresolved interpretation before analysis",
                    "SOURCE_FILE": p.name, "SOURCE_SHEET": source_sheet or "",
                    "SOURCE_SHA256": hashlib.sha256(p.read_bytes()).hexdigest(), "GENERATED_BY": "tametools init"}
    declared_settings = [str(k).upper() for k in definitions.get("SETTINGS", {})]
    meta["SETTINGS"] = {"VALIDATE_ERROR": "KEEP", "CRR": "VALUE", **definitions.get("SETTINGS", {})}
    meta["INIT"] = {"REVIEW_VERSION": 2, "TYPES": physical_types, "DECLARED_SETTINGS": declared_settings,
                    "DEFINITIONS_FILE": definitions_file.name if definitions_file else "",
                    "DEFINITIONS_SHA256": hashlib.sha256(definitions_file.read_bytes()).hexdigest() if definitions_file else ""}
    dataset = TameDataset(frame, specs, meta=meta, source_path=str(p))
    validate_options(dataset)
    input_profiles = semantic_profiles(dataset)
    original_dataset = dataset.replace(df=dataset.df.copy(), meta=deepcopy(dataset.meta), columns=list(dataset.columns))
    if interactive:
        dataset = configure_interactively(dataset, read=input_fn, emit=output_fn)
    # Export only input-column declarations, before adding derived/raw columns.
    from .init_review import ALLOWED_DEFINITIONS
    decisions = {k: deepcopy(v) for k, v in dataset.meta.items() if k in ALLOWED_DEFINITIONS}
    # Effective default settings must not become falsely "reviewed" on replay.
    decisions["SETTINGS"] = {k: v for k, v in decisions.get("SETTINGS", {}).items() if k in declared_settings}
    for column in dataset.columns:
        decisions.setdefault("COLUMN", {}).setdefault(column.name, {})["TAGS"] = list(column.tags)
    decisions_text = "# Replay init choices with --no-interactive --definitions this-file.toml\n" + dumps(decisions)
    decisions_hash = hashlib.sha256(decisions_text.encode("utf-8")).hexdigest()
    decisions_path = out.with_suffix(".init-decisions.toml")
    # Protect previously reviewed choices: different content gets a separate file.
    if decisions_path.exists() and decisions_path.read_text(encoding="utf-8") != decisions_text:
        decisions_path = out.with_suffix(f".init-decisions-{decisions_hash[:12]}.toml")
        if decisions_path.exists() and decisions_path.read_text(encoding="utf-8") != decisions_text:
            raise ValueError(f"Init decisions file already exists with different content: {decisions_path}")
    _, template = review_dataset(dataset, declared_settings=declared_settings)
    dataset, transformations = apply_options(dataset)
    output_profiles = semantic_profiles(dataset)
    dataset.meta["INIT"].update(INTERACTIVE=interactive, DECISIONS_SHA256=decisions_hash,
                                 DECISIONS_FILE=decisions_path.name, TRANSFORMATIONS=transformations)
    issues, _ = review_dataset(dataset, declared_settings=declared_settings)
    pending = [i for i in issues if i["status"] == "NEEDS_DEFINITION"]
    required = [i for i in pending if i["required"]]
    dataset.meta["INIT"]["REVIEW_REQUIRED"] = bool(required)
    dataset.meta["INIT"]["ISSUES"] = issues
    dataset = append_log_entry(dataset, action="INIT", message=f"starter and interpretation review generated from {p.name}",
        input_dataset=original_dataset,
        parameters={"SOURCE_FILE": p.name, "SOURCE_SHEET": source_sheet or "", "SOURCE_SHA256": meta["INFO"]["SOURCE_SHA256"],
                    "ROWS": len(df), "INPUT_COLUMNS": len(specs), "REQUIRED_DEFINITIONS": len(required),
                    "DEFINITIONS_SHA256": meta["INIT"]["DEFINITIONS_SHA256"], "DECISIONS_SHA256": decisions_hash,
                    "OPTIONS": dataset.meta.get("INIT_OPTIONS", {}), "TRANSFORMATIONS": transformations})
    write_tame(out, dataset, standardize_sex_values=False)
    review_path = out.with_suffix(".init-review.json")
    template_path = out.with_suffix(".definitions.toml")
    report = {"input": str(p), "output": str(out), "source_sha256": meta["INFO"]["SOURCE_SHA256"],
              "rows": len(df), "issues": issues, "required_definitions": len(required), "unresolved_definitions": len(pending),
              "default_comparator_policy": "VALUE", "declared_settings": declared_settings,
              "definitions_sha256": meta["INIT"]["DEFINITIONS_SHA256"],
              "input_profiles": input_profiles, "output_profiles": output_profiles,
              "transformations": transformations, "decisions_path": str(decisions_path), "decisions_sha256": decisions_hash,
              "note": "Basic summaries do not resolve missing source meanings, units, or blank-cell reasons."}
    review_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Keep the exact bytes covered by DECISIONS_SHA256 on Windows as well as POSIX.
    decisions_path.write_bytes(decisions_text.encode("utf-8"))
    # Preserve a template already edited by the user, including the applied definition file.
    if not template_path.exists():
        template_path.write_text(template_text(template), encoding="utf-8")
    col_info = [(c.name, "::".join(c.tags), physical_types.get(c.name, "CATEGORY" if c.has_tag("CATEGORY") else "STR")) for c in dataset.columns]
    return {"sheet": source_sheet, "columns": col_info, "review": report, "review_path": str(review_path),
            "definitions_template": str(template_path), "decisions_path": str(decisions_path)}
