"""아무 xlsx/csv 입력에서 시작용 .tame 를 자동 생성한다(태그·데이터타입 추정).

처음 사용자의 진입장벽을 낮추기 위해, 평범한 표를 받아 컬럼명과 **전체 값**을 근거로 가장 좁은
데이터 타입을 추정한 시작용 TAME 를 만든다. 다중 시트 xlsx 는 시트를 선택할 수 있고, 어떤 파일의
어떤 시트에서 생성되었는지 META.INFO 와 LOG(provenance)에 기록한다. 추정 데이터타입은 META 에도
기록한다. 부등호 허용 여부가 다른 ``<NUM>`` 과 ``NUM`` 은 반드시 구분한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

__all__ = ["list_sheets", "infer_narrow_type", "infer_column_tags", "init_from_table"]

_STRICT_NUM = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_COMPARATOR = re.compile(r"^[<>]=?\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_STATE_TOKEN = re.compile(r"^<<.*>>$")
_DATE_LIKE = re.compile(r"(?:\d{1,4}[-/.]\d{1,2}(?:[-/.]\d{1,4})?|\d{1,2}:\d{2})")
_LEADING_ZERO = re.compile(r"^0\d+$")

_ID = re.compile(r"(등록번호|검체번호|환자번호|\bid\b|accession|mrn|sid|pid|순번|번호)", re.IGNORECASE)
_SEX = re.compile(r"(성별|sex|gender)", re.IGNORECASE)
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
    if _NAME.search(name):
        return ("NAME", "STR")
    if _ID.search(name) or name.endswith(("ID", "Id", "_id", "_ID")):  # PatientID/SampleID 등 camelCase
        return ("ID", "STR")
    if _HOSP.search(name):
        return ("HOSPITAL_ID", "STR")
    if _SEX.search(name):
        return ("SEX",)
    if _AGE.search(name):
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
        return pd.read_excel(path, sheet_name=sheet if sheet is not None else 0, dtype=str)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str)
    return pd.read_csv(path, dtype=str)


def _toml_quote(key: str) -> str:
    return '"' + str(key).replace("\\", "\\\\").replace('"', '\\"') + '"'


def init_from_table(input_path: str | Path, output_path: str | Path, *, sheet: str | None = None) -> dict:
    """xlsx/csv/tsv → 시작용 .tame 생성. 어떤 파일/시트에서 왔는지 META.INFO·LOG 에 기록한다.

    반환: ``{"sheet": 시트명|None, "columns": [(이름, 태그, 데이터타입), ...]}``.
    """
    from .io import read_tame, write_tame
    from .provenance import append_log_entry

    p = Path(input_path)
    df = _read_table(p, sheet)

    headers: list[str] = []
    col_info: list[tuple[str, str, str]] = []
    type_lines: list[str] = []
    for col in df.columns:
        name = str(col)
        tags = infer_column_tags(name, df[col])
        dtype = infer_narrow_type(df[col])
        tag_str = "::".join(tags)
        headers.append(f"[[{tag_str}]]{name}")
        col_info.append((name, tag_str, dtype))
        type_lines.append(f"{_toml_quote(name)} = {_toml_quote(dtype)}")

    source_sheet = sheet if sheet is not None else (list_sheets(p) or [None])[0]
    sheet_toml = _toml_quote(source_sheet) if source_sheet else '""'
    info = (
        "<META>\n[INFO]\n"
        'DESCRIPTION = "auto-generated starter TAME by tametools init; review header tags before use"\n'
        f"SOURCE_FILE = {_toml_quote(p.name)}\n"
        f"SOURCE_SHEET = {sheet_toml}\n"
        'GENERATED_BY = "tametools init"\n'
        "[SETTINGS]\nVALIDATE_ERROR = \"KEEP\"\nCRR = \"VALUE\"\n"
        "[INIT.TYPES]\n" + "\n".join(type_lines) + "\n"
        "</META>\n"
    )
    body = "<DATA>\n" + "\t".join(headers) + "\n"
    for _, row in df.iterrows():
        body += "\t".join("" if pd.isna(v) else str(v) for v in row) + "\n"
    body += "</DATA>\n"

    out = Path(output_path)
    out.write_text(info + body, encoding="utf-8")

    # provenance: 어떤 파일/시트에서 생성되었는지 LOG 에 기록
    dataset = read_tame(out)
    dataset = append_log_entry(
        dataset,
        action="INIT",
        message=f"starter TAME generated from {p.name}" + (f" sheet '{source_sheet}'" if source_sheet else ""),
        parameters={"SOURCE_FILE": p.name, "SOURCE_SHEET": source_sheet or "", "ROWS": int(len(df)), "COLUMNS": int(df.shape[1])},
    )
    write_tame(out, dataset)
    return {"sheet": source_sheet, "columns": col_info}
