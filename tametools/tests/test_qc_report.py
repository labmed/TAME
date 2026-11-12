"""QC report with Levey-Jennings charts, written by `tametools sync`."""
import importlib
import importlib.util
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pandas as pd
import pytest

from tametools.io import read_tame
from tametools.plugin_base import list_plugins

list_plugins()      # WESTGARD_QC is a plugin package: use its modules as tametools loads them
_qc_report = importlib.import_module("tametools_dropin_westgard_qc.report")
lj_chart_svg = _qc_report.lj_chart_svg
render_qc_report = _qc_report.render_qc_report
report_options = _qc_report.report_options
from tametools.sync import SyncError, sync_project, sync_state

ROOT = Path(__file__).resolve().parents[2]
TUTORIAL = ROOT / "tutorial" / "26_qc_sync"
_spec = importlib.util.spec_from_file_location("qc_demo_data", TUTORIAL / "qc_demo_data.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

DAY_20, DAY_21, DAY_21_PM = "2026-10-31T09:00:00+09:00", "2026-11-01T09:00:00+09:00", "2026-11-01T14:00:00+09:00"


@pytest.fixture
def lab(tmp_path):
    project = tmp_path / "qc_chem.tame"
    shutil.copy(TUTORIAL / "qc_chem.tame", project)
    excel = demo.write_workbook(tmp_path / "qc_chem_daily.xlsx")
    return SimpleNamespace(dir=tmp_path, project=project, excel=excel, result=tmp_path / "qc_chem_result.tame",
                           report=tmp_path / "qc_chem_report.html")


def rewrite(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def charts(html):
    """Every Levey-Jennings chart in the page, parsed as XML (so the markup is well formed)."""
    return [ET.fromstring(fragment) for fragment in re.findall(r"<svg class='lj' viewBox.*?</svg>", html, flags=re.S)]


def points(chart):
    return [group for group in chart.iter("g") if group.get("class") == "pt"]


def test_sync_writes_a_self_contained_report_with_one_chart_per_control_material(lab):
    first = sync_project(lab.project, now=DAY_20)
    assert first.report_file == "qc_chem_report.html" and lab.report.is_file()
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(lab.project, now=DAY_21)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_2)
    report = sync_project(lab.project, now=DAY_21_PM)
    html = lab.report.read_text(encoding="utf-8")

    assert html.startswith("<!doctype html>") and "<title>임상화학 일일 정도관리 보고서</title>" in html
    assert not re.search(r"""(?:src|href)\s*=|url\(|https?://""", html)          # no external assets or links
    for heading in ("1. 이번 동기화", "2. 검사·수준별 현황", "3. Levey-Jennings 관리도", "4. 경고·거부 목록과 조치 기록", "5. 적용한 규칙과 기준값", "6. 동기화 이력", "7. 검토 기록"):
        assert f">{heading}</h2>" in html
    assert "수동 검토용" in html and "동기화 <b>#3 (2026-11-01 14:00)</b>" in html and "ALT, Creatinine, Glucose" in html

    drawn = charts(html)
    assert len(drawn) == 6                                                           # 3 analytes x 2 levels
    assert [len(points(chart)) for chart in drawn] == [21] * 6                       # every run of the window, on every chart
    assert sum(len(list(chart.iter("polyline"))) for chart in drawn) == 6
    marks = [path.get("class") for chart in drawn for path in chart.iter("path")]
    assert marks.count("rej") == 5 and marks.count("warn") == 2                      # as in the result table
    ringed = sum(1 for chart in drawn for circle in chart.iter("circle") if circle.get("class") == "new")
    assert ringed == report.result["new"] + report.result["changed"] == 5
    labels = [text.text for chart in drawn for text in chart.iter("text") if text.get("class") == "flag"]
    assert sorted(labels) == ["10x", "1_2s", "1_2s", "2_2s", "2_2s", "R_4s", "R_4s"]
    glucose_level_1 = drawn[4]
    assert [text.text for text in glucose_level_1.iter("text")][:2] == ["84", "-3SD"]   # limit lines carry the value and the SD multiple
    assert "2026-11-01 08:30 · 95 mg/dL · z +2.50 · 거부 · 2_2s, 1_2s" in [group.find("title").text for group in points(glucose_level_1)]

    # The tiles and the list of what changed agree with the sync report.
    tiles = dict(re.findall(r"<div class='label'>(.*?)</div><div class='value'>(.*?)</div>", html))
    assert tiles == {"원본에 추가된 행": "1", "새 결과": "3", "새 결과 중 거부": "2", "새 결과 중 경고": "0", "달라진 기존 결과": "2"}
    assert "측정 판정: 경고 → <b>거부</b>" in html and "거부 규칙: 없음 → <b>R_4s</b>" in html
    assert html.count("<td class='nw'>새 결과</td>") == 3 and html.count("<td class='nw'>변경</td>") == 2


def test_report_statistics_rules_and_baselines_match_the_result(lab):
    sync_project(lab.project, now=DAY_20)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(lab.project, now=DAY_21)
    html = lab.report.read_text(encoding="utf-8")
    result = read_tame(lab.result).df
    level_1 = result.loc[result.result_id.eq("glucose") & result.level.eq("1")]
    assert len(level_1) == 21 and level_1.run_decision.eq("REJECT").sum() == 1
    values = pd.to_numeric(level_1.loc[level_1.run_decision.ne("REJECT"), "value"])   # values of rejected runs are left out of the statistics
    row = re.search(r"<tr[^>]*><td class=''>Glucose</td><td class=''>1</td>(.*?)</tr>", html, flags=re.S).group(1)
    cells = re.findall(r"<td class='[a-z]*'>(.*?)</td>", row, flags=re.S)
    assert cells[:9] == ["45871", "90 ± 2 mg/dL", "2.2", "20<div class='small'>거부 run 제외 1</div>", f"{values.mean():.1f}",
                         f"{values.std(ddof=1):.2f}", f"{100 * values.std(ddof=1) / values.mean():.1f}",
                         f"{(values.mean() - 90) / 2:+.2f}", f"{values.std(ddof=1) / 2:.2f}"]
    assert cells[9:11] == ["1", "1"]                                                # one warning (31 Oct) and one rejection (1 Nov)
    # Declared limits that the accepted data do not support are pointed out (creatinine: observed SD under half the declared SD).
    assert "기준값 점검: Creatinine 수준 1 (lot 45871): 관측 SD 가 기준 SD 의 0.44배입니다" in html
    assert "거부된 run 이후: 그 검사의 연속 계수를 처음부터 다시 시작(RESTART)" in html
    # Section 5 lists each rule with what it does for each analyte, and every declared baseline.
    assert "<td class='nw'><b>10x</b></td><td class=''>연속 10개가 평균의 같은 쪽</td><td class='nw'>계통오차</td>" in html
    assert html.count("Synthetic example baseline") == 6
    assert "0.90</td><td class='num'>0.03</td><td class='num'>3.3</td>" in html      # creatinine level 1: mean, SD, CV%
    # Section 4 keeps a blank column for the action taken.
    assert "원인·조치 기록" in html and "조치 기록 없음" in html and "10x: 같은 물질의 연속 run" in html


def test_report_follows_per_analyte_rules_and_report_options(lab):
    rewrite(lab.project, "LAST_RUNS = 30 ", "LAST_RUNS = 10 ")
    rewrite(lab.project, "[WESTGARD_QC.REPORT]", '[WESTGARD_QC.RULE_SETTINGS."10x"]\nACTION = "WARNING"\nSCOPE = "WITHIN_MATERIAL"\n\n'
            '[WESTGARD_QC.BY_RESULT.glucose]\nRULES = ["1_3s", "2_2s", "R_4s"]\n\n[WESTGARD_QC.REPORT]')
    sync_project(lab.project, now=DAY_20)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    report = sync_project(lab.project, now=DAY_21)
    new = report.new_rows.set_index("result_id")
    assert (new.loc["creatinine", "violation"], new.loc["creatinine", "warning"], new.loc["creatinine", "decision"]) == ("NONE", "10x", "WARNING")
    assert new.loc["glucose", "violation"] == "2_2s"
    html = lab.report.read_text(encoding="utf-8")
    assert [len(points(chart)) for chart in charts(html)] == [10, 9, 10, 9, 10, 9]   # last 10 runs; level 2 not yet measured in the last one
    assert "검사·장비별 최근 10개 run 기준" in html
    def matrix(rule):                                                               # cells for ALT, Creatinine, Glucose
        cells = re.search(rf"<b>{rule}</b></td>(.*?)</tr>", html, flags=re.S).group(1)
        return re.findall(r"<td class=''>(.*?)</td>", cells)[1:]
    assert matrix("10x") == ["경고<div class='small'>같은 물질의 연속 run 만</div>"] * 3      # the per-rule setting applies to every analyte
    assert matrix("4_1s") == ["거부<div class='small'>물질 내 연속 + 수준 간</div>"] * 2 + ["—"]  # glucose has its own rejection rules
    assert matrix("1_3s") == ["거부"] * 3
    plugin = read_tame(lab.result).meta["PLUGIN"]
    assert plugin["REPORT"] == {"TITLE": "임상화학 일일 정도관리 보고서", "LAST_RUNS": 10}
    assert plugin["RULESETS"]["glucose"]["RULES"] == ["1_3s", "2_2s", "R_4s"] and plugin["RULESETS"]["alt"]["WARNING_RULES"] == ["1_2s", "10x"]


def test_report_is_part_of_the_all_or_nothing_sync(lab):
    sync_project(lab.project, now=DAY_20)
    before = lab.report.read_bytes()
    assert sync_project(lab.project, now=DAY_21).status == "UNCHANGED" and lab.report.read_bytes() == before
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    assert sync_project(lab.project, now=DAY_21, dry_run=True).report_file == "" and lab.report.read_bytes() == before
    demo.edit_cell(lab.excel, day=5, level="1", column="Glucose", value=97)
    assert sync_project(lab.project, now=DAY_21).status == "BLOCKED" and lab.report.read_bytes() == before
    accepted = sync_project(lab.project, now=DAY_21, accept_changes=True)
    html = lab.report.read_text(encoding="utf-8")
    assert accepted.report_file and "원본 수정(확인 후 반영)" in html and "Glucose: 92 → 97" in html
    assert sync_state(read_tame(lab.project))["REPORT"] == "qc_chem_report.html"

    lab.report.unlink()                                                             # a missing report is written again
    again = sync_project(lab.project, now=DAY_21_PM)
    assert again.status == "COMMITTED" and "보고서 파일을 다시 만들어야 합니다" in again.reasons and lab.report.is_file()
    assert not list(lab.dir.glob(".*sync-tmp*"))


def test_report_configuration_errors(lab, tmp_path):
    rewrite(lab.project, 'REPORT = "qc_chem_report.html"', 'REPORT = "qc_chem_report.pdf"')
    with pytest.raises(SyncError, match=r"REPORT 는 \.html"):
        sync_project(lab.project)
    rewrite(lab.project, 'REPORT = "qc_chem_report.pdf"', 'REPORT = "qc_chem_result.tame"')
    with pytest.raises(SyncError, match="REPORT 는 .html|달라야"):
        sync_project(lab.project)
    with pytest.raises(ValueError, match="LAST_RUNS"):
        report_options({"LAST_RUNS": 1})
    with pytest.raises(ValueError, match="Unknown WESTGARD_QC.REPORT option"):
        report_options({"ROWS": 5})
    assert report_options({"last_runs": "45", "title": "월간 QC"}) == {"LAST_RUNS": 45, "TITLE": "월간 QC"}


def frame(**overrides):
    rows = []
    for run, (value, decision, violation, warning) in enumerate([(90.0, "IN_CONTROL", "NONE", "NONE"), (None, "NOT_EVALUATED", "NE", "NE"),
                                                                 (101.0, "REJECT", "1_3s", "1_2s"), (94.6, "WARNING", "NONE", "1_2s")], 1):
        rows.append({"result_id": "glucose", "instrument": "A-1", "lot": "L<1>", "level": "1 & 2", "run": f"r{run}",
                     "time": f"2026-01-0{run}T08:00:00+09:00", "value": value, "unit": "mg/dL", "mean": 90.0, "sd": 2.0,
                     "z": None if value is None else (value - 90) / 2, "violation": violation, "warning": warning,
                     "rule_scope": "NONE", "error_type": "NONE", "decision": decision, "run_decision": decision,
                     "baseline_key": "b1", "baseline_reference": "<script>alert(1)</script>", "baseline_valid_from": "2026-01-01",
                     "baseline_valid_to": "2027-01-01", "source_result_column": "혈당 \"Glu\"", **overrides})
    return pd.DataFrame(rows)


def test_missing_results_extreme_values_and_untrusted_text_are_drawn_safely():
    html = render_qc_report(frame(), settings={"RULES": ["1_3s"], "WARNING_RULES": ["1_2s"]}, title="A <b>title</b>")
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<title>A &lt;b&gt;title&lt;/b&gt;</title>" in html and "lot L&lt;1&gt;" in html and "수준 1 &amp; 2" in html
    assert "1. 이번 동기화" not in html and "6. 동기화 이력" not in html           # no sync information was given
    (chart,) = charts(html)
    kinds = [path.get("class") for path in chart.iter("path")]
    assert kinds == ["missing", "rej", "warn"]
    assert [text.text for text in chart.iter("text") if text.get("class") == "flag"] == ["1_3s (+5.50)", "1_2s"]   # beyond the +/-4 SD frame: value in the label
    assert len(list(chart.iter("polyline"))) == 1                                   # the missing result breaks the line; one segment remains
    titles = [group.find("title").text for group in points(chart)]
    assert titles[1] == "2026-01-02 08:00 · 결과 없음 · z — · 미평가"
    assert "결과 없음</td>" in html                                                  # the missing result is listed for review


def test_single_chart_can_be_drawn_without_the_page():
    data = frame().assign(_run_index=range(4), sync_status="")
    svg = lj_chart_svg(data, 4)
    assert ET.fromstring(svg).get("viewBox") == "0 0 940 210"
    assert render_qc_report(frame().iloc[0:0], title="empty").count("아직 관리물질 측정이 없습니다") == 1


def test_results_without_a_report_of_their_own_get_the_plain_report(tmp_path):
    project = tmp_path / "log.tame"
    project.write_text('<META>\n[SYNC]\nSOURCE = "log.csv"\nWORK = "CHECK"\nRESULT = "log_result.tame"\nREPORT = "log_report.html"\n\n'
                       '[WORKS]\nCHECK = ["VALIDATE"]\n\n[COLUMN.sample]\nTAGS = ["STR"]\n\n[COLUMN.value]\nTAGS = ["NUM"]\n</META>\n'
                       "<DATA>\nsample\tvalue\n</DATA>\n", encoding="utf-8")
    (tmp_path / "log.csv").write_text("sample,value\nA,1.0\n<b>B</b>,2.5\n", encoding="utf-8")
    report = sync_project(project, now=DAY_20)
    html = (tmp_path / "log_report.html").read_text(encoding="utf-8")
    assert report.report_file == "log_report.html" and "새 결과 (2행)" in html
    assert "&lt;b&gt;B&lt;/b&gt;" in html and "<b>B</b>" not in html and "전용 보고서를 제공하지 않습니다" in html
