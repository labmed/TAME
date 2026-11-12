"""Synthetic daily clinical-chemistry QC log for the sync tutorial and its tests.

Not patient data and not real control results: the values are chosen so that
each Westgard rule in the scenario has one known, hand-checkable cause.

One row = one control level measured in one daily run (08:30).

    측정일시 | 장비 | Lot | Level | Glucose | Creatinine | ALT | 비고

Declared baselines (see qc_chem.tame):

    analyte      level 1 (lot 45871)   level 2 (lot 45872)
    Glucose      90 +/- 2 mg/dL        280 +/- 5 mg/dL
    Creatinine   0.90 +/- 0.03 mg/dL   5.20 +/- 0.12 mg/dL
    ALT          32 +/- 1.5 U/L        120 +/- 4 U/L
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl

SHEET = "QC"
HEADERS = ["측정일시", "장비", "Lot", "Level", "Glucose", "Creatinine", "ALT", "비고"]
INSTRUMENT = "AU5800-1"
LOTS = {"1": "45871", "2": "45872"}
FIRST_DAY = dt.datetime(2026, 10, 12, 8, 30)
BASELINE_DAYS = 20  # 2026-10-12 .. 2026-10-31

# Twenty in-control days. The only earlier signal is a single 1_2s warning (day 8, level 2 glucose).
LEVEL_1 = {
    "Glucose":    [90, 91, 89, 90, 92, 88, 91, 90, 89, 91, 90, 92, 89, 90, 91, 88, 90, 91, 92, 95],
    "Creatinine": [0.90, 0.89, 0.91, 0.88, 0.90, 0.92, 0.89, 0.91, 0.90, 0.88, 0.89,
                   0.91, 0.92, 0.91, 0.91, 0.92, 0.91, 0.92, 0.92, 0.91],
    "ALT":        [32, 33, 31, 32, 30, 33, 32, 34, 31, 32, 33, 30, 32, 31, 33, 32, 34, 31, 32, 33],
}
LEVEL_2 = {
    "Glucose":    [280, 283, 277, 279, 284, 276, 281, 291, 278, 282, 280, 277, 283, 279, 281, 284, 276, 280, 282, 279],
    "Creatinine": [5.20, 5.25, 5.14, 5.22, 5.17, 5.28, 5.19, 5.23, 5.12, 5.21,
                   5.26, 5.15, 5.24, 5.18, 5.27, 5.16, 5.22, 5.13, 5.25, 5.17],
    "ALT":        [120, 123, 117, 121, 118, 124, 119, 122, 116, 121, 125, 118, 122, 119, 123, 117, 121, 124, 118, 122],
}

# Day 21 (2026-11-01), the "next day" of the scenario.
#   Level 1, entered first:
#     Glucose 95    z=+2.5; day 20 was also +2.5      -> 2_2s across runs (reject), 1_2s
#     Creatinine .92 z=+0.67; 10th day above the mean  -> 10x (reject)
#     ALT 36        z=+2.67; alone                     -> 1_2s (warning only)
#   Level 2, entered later the same day:
#     Glucose 291   z=+2.2 with level 1 at +2.5        -> 2_2s within the run
#     Creatinine 5.18 z=-0.17                          -> in control
#     ALT 110       z=-2.5 with level 1 at +2.67       -> R_4s (reject); level 1 ALT changes from warning to reject
NEXT_DAY = FIRST_DAY + dt.timedelta(days=BASELINE_DAYS)
NEXT_DAY_LEVEL_1 = {"측정일시": NEXT_DAY, "장비": INSTRUMENT, "Lot": LOTS["1"], "Level": "1",
                    "Glucose": 95, "Creatinine": 0.92, "ALT": 36, "비고": None}
NEXT_DAY_LEVEL_2 = {"측정일시": NEXT_DAY, "장비": INSTRUMENT, "Lot": LOTS["2"], "Level": "2",
                    "Glucose": 291, "Creatinine": 5.18, "ALT": 110, "비고": None}


def baseline_rows() -> list[dict]:
    rows = []
    for day in range(BASELINE_DAYS):
        moment = FIRST_DAY + dt.timedelta(days=day)
        for level, values in (("1", LEVEL_1), ("2", LEVEL_2)):
            rows.append({"측정일시": moment, "장비": INSTRUMENT, "Lot": LOTS[level], "Level": level,
                         "Glucose": values["Glucose"][day], "Creatinine": values["Creatinine"][day],
                         "ALT": values["ALT"][day],
                         "비고": "1_2s 확인" if (day, level) == (7, "2") else None})
    return rows


def write_workbook(path: str | Path, rows: list[dict] | None = None) -> Path:
    """Create the QC workbook the way a laboratory keeps it: typed dates and numbers, one sheet."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = SHEET
    sheet.append(HEADERS)
    for row in baseline_rows() if rows is None else rows:
        sheet.append([row[name] for name in HEADERS])
    workbook.save(path)
    return Path(path)


def append_measurement(path: str | Path, row: dict) -> None:
    """What the laboratory does each day: open the same workbook and add one row at the bottom."""
    workbook = openpyxl.load_workbook(path)
    workbook[SHEET].append([row[name] for name in HEADERS])
    workbook.save(path)


def edit_cell(path: str | Path, *, day: int, level: str, column: str, value) -> None:
    """Change an already recorded value (1-based day), as a late correction would."""
    workbook = openpyxl.load_workbook(path)
    sheet = workbook[SHEET]
    target = FIRST_DAY + dt.timedelta(days=day - 1)
    for cells in sheet.iter_rows(min_row=2):
        if cells[0].value == target and str(cells[3].value) == level:
            cells[HEADERS.index(column)].value = value
            workbook.save(path)
            return
    raise ValueError("No such QC row")
