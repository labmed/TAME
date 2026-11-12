"""Daily QC with one Excel file, one project .tame and one result file.

Run from anywhere:

    python tutorial/26_qc_sync/run_demo.py

It rebuilds ``outputs/`` next to this script and runs the same commands a
laboratory would type, printing what each ``tametools sync`` reports:

    1. day 20   first sync of the workbook (40 control measurements)
    2. day 20   sync again: nothing changed, nothing written
    3. day 21   ONE new row (level 1) -> Westgard multirule on the new measurement
    4. day 21   level 2 of the same run arrives -> rows already reported change
    5. later    an old value is corrected in Excel -> stopped until accepted
    6.          the history kept inside the project
    7.          rejected runs without an action record; record one (kept in the project, not in Excel)
    8.          record who reviewed the period
    9.          sync again: nothing to do, and a reminder of what is still open
"""
from __future__ import annotations

from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tametools" / "src"))

import qc_demo_data as demo  # noqa: E402
from tametools.cli import _main  # noqa: E402

OUTPUTS = HERE / "outputs"
PROJECT = OUTPUTS / "qc_chem.tame"
EXCEL = OUTPUTS / "qc_chem_daily.xlsx"


def run(title: str, command: str, *options: str) -> int:
    shown = " ".join(f'"{option}"' if " " in option else option for option in options)
    print(f"\n{'=' * 100}\n{title}\n$ tametools {command} qc_chem.tame {shown}".rstrip())
    code = _main([command, str(PROJECT), *options])
    print(f"(exit code {code})")
    return code


def sync(title: str, *options: str) -> int:
    return run(title, "sync", *options)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if OUTPUTS.exists():
        shutil.rmtree(OUTPUTS)
    OUTPUTS.mkdir()
    shutil.copy(HERE / "qc_chem.tame", PROJECT)
    demo.write_workbook(EXCEL)

    sync("1) 20일째: 처음 동기화 (엑셀 40행)")
    sync("2) 같은 날 다시 실행: 변경 없음")

    demo.append_measurement(EXCEL, demo.NEXT_DAY_LEVEL_1)
    sync("3) 다음날: 엑셀에 Level 1 측정 1행 추가")

    demo.append_measurement(EXCEL, demo.NEXT_DAY_LEVEL_2)
    sync("4) 같은 날 오후: 같은 run 의 Level 2 측정 1행 추가")

    demo.edit_cell(EXCEL, day=5, level="1", column="Glucose", value=97)
    sync("5) 이미 동기화한 10월 16일 Glucose 값을 엑셀에서 92 -> 97 로 정정: 확인 전까지 중단")
    sync("   의도한 정정임을 확인하고 반영", "--accept-changes")

    sync("6) 프로젝트 안에 기록된 동기화 이력", "--history")

    run("7) 조치 기록이 없는 거부 run 확인", "qc-action", "--list")
    run("   11월 1일 Glucose 거부에 대한 조치를 기록 (엑셀이 아니라 프로젝트 파일에 남음)", "qc-action",
        "--run", "2026-11-01", "--result", "Glucose", "--cause", "시약 lot 교체 후 두 수준이 함께 상승",
        "--action", "재보정 후 관리물질 재측정", "--outcome", "10:05 재측정: 수준 1 91, 수준 2 282 (관리 내)",
        "--patient-impact", "보고 전 발견, 환자 검체 재검 없음", "--by", "홍길동")
    run("8) 기간 검토를 기록", "qc-review", "--from", "2026-10-12", "--to", "2026-11-01", "--by", "김책임",
        "--note", "미조치 거부는 다음 근무일까지 기록")
    sync("9) 다시 동기화: 바뀐 것은 없고, 남은 미조치 거부를 알려 줌")
    print(f"\n파일: {OUTPUTS}")
    for path in sorted(OUTPUTS.iterdir()):
        if path.is_file():
            print(f"  {path.name:24s} {path.stat().st_size:>8,d} bytes")


if __name__ == "__main__":
    main()
