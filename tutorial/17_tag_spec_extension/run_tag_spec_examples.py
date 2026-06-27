from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from pathlib import Path
import os
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tutorial" / "17_tag_spec_extension"
DEFAULT_OUT = HERE / "outputs"
CUSTOM_AGE5 = HERE / "custom_tags_age5.tame"
WIDE_PIVOT = HERE / "wide_pivot_context.tame"


def command(*args: str) -> list[str]:
    if shutil.which("tametools"):
        return ["tametools", *args]
    return [sys.executable, "-m", "tametools", *args]


def path_arg(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_steps(output_dir: Path) -> list[tuple[str, str, list[str], Path]]:
    return [
        (
            "01. AGE5 사용자 정의 태그 review",
            "AGE5가 AGE를 상속하고 AGE_BIN_WIDTH=5가 review 분포에 적용되는지 확인한다.",
            command("review", path_arg(CUSTOM_AGE5)),
            output_dir / "01_review_age5.stdout.txt",
        ),
        (
            "02. RESULT_INDEX 파생 태그 생성",
            "ACTION_PIPELINE이 RESULT/REF_HIGH 태그 selector로 파생 컬럼을 만들고 RESULT_INDEX 태그를 부여하는지 확인한다.",
            command(
                "run-action-pipeline",
                path_arg(CUSTOM_AGE5),
                "DEFAULT",
                "--output",
                path_arg(output_dir / "custom_tags_age5_result.tame"),
            ),
            output_dir / "02_result_index.stdout.txt",
        ),
        (
            "03. AGE5 기반 연령/성별 임상화학 분석",
            "CHEMISTRY_ANALYSIS AGE_SEX_RESULT가 AGE5의 5세 구간을 사용하는지 확인한다.",
            command(
                "run-plugin",
                path_arg(CUSTOM_AGE5),
                "CHEMISTRY_ANALYSIS",
                "--option",
                "MODE=AGE_SEX_RESULT",
                "--output",
                path_arg(output_dir / "age_sex_result.tame"),
            ),
            output_dir / "03_age_sex_result.stdout.txt",
        ),
        (
            "04. PIVOT_CONTEXT 기반 wide RESULT 이상 플래그",
            "ABNORMAL_FLAG가 wide RESULT 컬럼별 PIVOT_CONTEXT.REF_LOW/REF_HIGH를 읽어 AST/ALT를 각각 판정하는지 확인한다.",
            command(
                "run-plugin",
                path_arg(WIDE_PIVOT),
                "ABNORMAL_FLAG",
                "--option",
                "MODE=FLAG",
                "--output",
                path_arg(output_dir / "wide_pivot_context_flagged.tame"),
            ),
            output_dir / "04_abnormal_flag.stdout.txt",
        ),
    ]


def run_step(title: str, purpose: str, args: list[str], stdout_path: Path, env: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    status = "PASS" if completed.returncode == 0 else f"FAIL returncode={completed.returncode}"
    section = [
        f"## {title}",
        "",
        purpose,
        "",
        "```bash",
        "$ " + " ".join(args),
        status,
        completed.stdout.strip(),
        "```",
        "",
        f"Captured stdout: `{stdout_path.name}`",
        "",
    ]
    return completed.returncode, "\n".join(section)


def validate_outputs(output_dir: Path) -> list[str]:
    sys.path.insert(0, str(ROOT / "tametools" / "src"))
    from tametools.io import read_tame

    checks: list[str] = []

    review_output = (output_dir / "01_review_age5.stdout.txt").read_text(encoding="utf-8")
    expected_distribution = "distribution_5y: 1-4: 2, 5-9: 2, 10-14: 2"
    if expected_distribution not in review_output:
        raise AssertionError(f"AGE5 review output did not contain {expected_distribution!r}.")
    checks.append("PASS: review AGE profile uses AGE_BIN_WIDTH=5 and prints distribution_5y.")

    derived = read_tame(output_dir / "custom_tags_age5_result.tame")
    if "상한대비비율" not in derived.df.columns:
        raise AssertionError("Derived RESULT_INDEX column was not created.")
    result_index = next((column for column in derived.columns if column.name == "상한대비비율"), None)
    if result_index is None or "RESULT_INDEX" not in result_index.tags:
        raise AssertionError("Derived column does not carry RESULT_INDEX tag.")
    checks.append("PASS: ACTION_PIPELINE created RESULT_INDEX derived column.")

    age_sex = read_tame(output_dir / "age_sex_result.tame")
    age_groups = set(age_sex.df["연령그룹"])
    if age_groups != {"1-4", "5-9", "10-14"}:
        raise AssertionError(f"Unexpected AGE_SEX_RESULT groups: {sorted(age_groups)}")
    checks.append("PASS: CHEMISTRY_ANALYSIS AGE_SEX_RESULT uses 5-year AGE5 bands.")

    flagged = read_tame(output_dir / "wide_pivot_context_flagged.tame")
    for column_name in ("AST_이상플래그", "ALT_이상플래그"):
        if column_name not in flagged.df.columns:
            raise AssertionError(f"Missing flag column: {column_name}")
        counts = Counter(str(value) for value in flagged.df[column_name])
        if counts != {"N": 2, "H": 1}:
            raise AssertionError(f"Unexpected {column_name} counts: {dict(counts)}")
    checks.append("PASS: ABNORMAL_FLAG uses per-column PIVOT_CONTEXT references for AST and ALT.")

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Run and self-validate tutorial 17 tag specification examples.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT), help="Directory for captured stdout and generated .tame outputs.")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'tametools' / 'src'}:{ROOT}"

    sections = ["# Tag spec extension execution log", ""]
    failed = False
    for title, purpose, cmd, stdout_path in build_steps(output_dir):
        returncode, section = run_step(title, purpose, cmd, stdout_path, env)
        sections.append(section)
        if returncode != 0:
            failed = True

    sections.append("## 05. 자가검증")
    sections.append("")
    sections.append("```text")
    if failed:
        sections.append("FAIL: one or more commands failed before self-validation.")
        sections.append("```")
        (output_dir / "execution_log.md").write_text("\n".join(sections), encoding="utf-8")
        return 1

    try:
        sections.extend(validate_outputs(output_dir))
        sections.append("```")
    except Exception as exc:
        sections.append(f"FAIL: {exc}")
        sections.append("```")
        (output_dir / "execution_log.md").write_text("\n".join(sections), encoding="utf-8")
        return 1

    log_path = output_dir / "execution_log.md"
    log_path.write_text("\n".join(sections), encoding="utf-8")
    print(log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
