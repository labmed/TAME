from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys
import zipfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tutorial" / "16_hands_on_guide"
OUT = HERE / "outputs"


def command(*args: str) -> list[str]:
    if shutil.which("tametools"):
        return ["tametools", *args]
    return [sys.executable, "-m", "tametools", *args]


STEPS: list[tuple[str, list[str]]] = [
    (
        "01. 태그 없는 원자료 review: 모든 컬럼이 단순 category처럼 보인다",
        command("review", str(HERE / "00_raw_lis.data.tame")),
    ),
    (
        "02. 태그 없는 상태에서 참고치 플러그인 실행: RESULT/SEX/AGE/ITEM role을 찾지 못한다",
        command("run-plugin", str(HERE / "00_raw_lis.data.tame"), "REFERENCE_INTERVAL"),
    ),
    (
        "03. META[COLUMN] 태그 적용 후 review: 같은 DATA가 임상 의미를 가진다",
        command("review", str(HERE / "00_raw_lis.data.tame"), "--meta", str(HERE / "01_basic_tags.meta.tame")),
    ),
    (
        "04. META 태그 적용 후 validate",
        command("validate", str(HERE / "00_raw_lis.data.tame"), "--meta", str(HERE / "01_basic_tags.meta.tame")),
    ),
    (
        "05. META 태그를 DATA 헤더 태그로 이동",
        command(
            "tags-to-header",
            str(HERE / "00_raw_lis.data.tame"),
            "--meta",
            str(HERE / "01_basic_tags.meta.tame"),
            "--output",
            str(OUT / "01_header_tagged.tame"),
        ),
    ),
    (
        "06. DATA 헤더 태그를 다시 META[COLUMN]으로 이동",
        command("tags-to-meta", str(OUT / "01_header_tagged.tame"), "--output", str(OUT / "01_meta_tagged_roundtrip.tame")),
    ),
    (
        "07. META[ACTION_PIPELINES].CLEAN 실행: 중복 제거, TAT, 결과비율, 연령군, 보험유형 join",
        command(
            "run-action-pipeline",
            str(HERE / "00_raw_lis.data.tame"),
            "CLEAN",
            "--meta",
            str(HERE / "02_actions_and_analyses.meta.tame"),
            "--output",
            str(OUT / "02_cleaned.tame"),
        ),
    ),
    (
        "08. 정제 결과 컬럼과 태그 확인",
        command("columns", str(OUT / "02_cleaned.tame")),
    ),
    (
        "09. 결과 요약통계",
        command(
            "run-plugin",
            str(OUT / "02_cleaned.tame"),
            "CHEMISTRY_ANALYSIS",
            "--option",
            "MODE=RESULT_SUMMARY",
            "--output",
            str(OUT / "03_result_summary.tame"),
        ),
    ),
    (
        "10. 참고구간 후보 산출",
        command("run-plugin", str(OUT / "02_cleaned.tame"), "REFERENCE_INTERVAL", "--output", str(OUT / "04_reference_interval.tame")),
    ),
    (
        "11. 참고치 대비 H/L/N 이상 플래그",
        command(
            "run-plugin",
            str(OUT / "02_cleaned.tame"),
            "ABNORMAL_FLAG",
            "--option",
            "MODE=FLAG",
            "--output",
            str(OUT / "05_abnormal_flag.tame"),
        ),
    ),
    (
        "12. 검사항목별 TAT 분석",
        command(
            "run-plugin",
            str(OUT / "02_cleaned.tame"),
            "CHEMISTRY_ANALYSIS",
            "--option",
            "MODE=TAT_BY_TEST",
            "--output",
            str(OUT / "06_tat_by_test.tame"),
        ),
    ),
    (
        "13. QC 정밀도와 chart contract 출력",
        command("run-plugin", str(OUT / "02_cleaned.tame"), "QC_ANALYSIS", "--option", "MODE=PRECISION", "--output", str(OUT / "07_qc_precision.tame")),
    ),
    (
        "14. 월별 결과 추세와 line chart contract 출력",
        command("run-plugin", str(OUT / "02_cleaned.tame"), "RESULT_TREND", "--option", "PERIOD=M", "--output", str(OUT / "08_result_trend.tame")),
    ),
    (
        "15. ROC 분석",
        command("run-plugin", str(OUT / "02_cleaned.tame"), "ROC_ANALYSIS", "--option", "POSITIVE=1", "--output", str(OUT / "09_roc.tame")),
    ),
    (
        "16. 기존 플러그인 재사용 보고서 생성: 표 + 차트 docx",
        command(
            "run-plugin",
            str(OUT / "02_cleaned.tame"),
            "COMBINED_CHEMISTRY_REPORT",
            "--option",
            f"REPORT_PATH={OUT / '10_combined_report.docx'}",
        ),
    ),
    (
        "17. 컬럼명이 바뀐 DATA도 같은 ACTION META로 정제",
        command(
            "run-action-pipeline",
            str(HERE / "00_raw_lis_renamed.data.tame"),
            "CLEAN",
            "--meta",
            str(HERE / "02_actions_and_analyses.meta.tame"),
            "--output",
            str(OUT / "12_cleaned_renamed.tame"),
        ),
    ),
    (
        "18. 컬럼명이 바뀐 DATA의 결과 요약통계",
        command(
            "run-plugin",
            str(OUT / "12_cleaned_renamed.tame"),
            "CHEMISTRY_ANALYSIS",
            "--option",
            "MODE=RESULT_SUMMARY",
            "--output",
            str(OUT / "13_renamed_clean_result_summary.tame"),
        ),
    ),
]


def run_step(title: str, args: list[str], env: dict[str, str]) -> str:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    status = "PASS" if completed.returncode == 0 else f"FAIL returncode={completed.returncode}"
    command_text = " ".join(args)
    return f"## {title}\n\n```bash\n$ {command_text}\n{status}\n{completed.stdout.strip()}\n```\n"


def compare_renamed_summary() -> str:
    sys.path.insert(0, str(ROOT / "tametools" / "src"))
    from tametools.io import read_tame

    original = read_tame(OUT / "03_result_summary.tame").df.sort_values("검사항목명").reset_index(drop=True)
    renamed = read_tame(OUT / "13_renamed_clean_result_summary.tame").df.sort_values("검사항목명").reset_index(drop=True)
    pd.testing.assert_frame_equal(original, renamed, check_dtype=False)
    return "PASS: original cleaned summary and renamed cleaned summary are identical."


def check_report_docx() -> str:
    report = OUT / "10_combined_report.docx"
    with zipfile.ZipFile(report) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/") and name.endswith(".png")]
    if not media:
        raise AssertionError("No chart PNG was embedded in the generated docx.")
    return f"PASS: {report} contains {', '.join(media)}."


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'tametools' / 'src'}:{ROOT}"

    sections = ["# Hands-on execution log\n"]
    for title, args in STEPS:
        sections.append(run_step(title, args, env))

    sections.append("## 19. 컬럼명 변경 결과 비교\n\n```text\n")
    try:
        sections.append(compare_renamed_summary())
        sections.append("\n")
        sections.append(check_report_docx())
        sections.append("\n```\n")
    except Exception as exc:
        sections.append(f"FAIL: {exc}\n```\n")
        (OUT / "execution_log.md").write_text("\n".join(sections), encoding="utf-8")
        return 1

    log_path = OUT / "execution_log.md"
    log_path.write_text("\n".join(sections), encoding="utf-8")
    print(log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
