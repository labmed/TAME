"""`tametools sync`: one growing source, one project .tame and one result file.

The main scenario is a daily clinical-chemistry QC workbook: twenty days are
synced, then the next day's control measurement is added as one more row.
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import openpyxl
import pandas as pd
import pytest

from tametools.io import read_tame, read_xlsx
from tametools.provenance import log_entries
from tametools.provenance_audit import verify_history
from tametools.sync import SyncError, format_report, splice_managed_meta, sync_history, sync_project, sync_state

ROOT = Path(__file__).resolve().parents[2]
TUTORIAL = ROOT / "tutorial" / "26_qc_sync"
_spec = importlib.util.spec_from_file_location("qc_demo_data", TUTORIAL / "qc_demo_data.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

DAY_20 = "2026-10-31T09:00:00+09:00"
DAY_21 = "2026-11-01T09:00:00+09:00"
DAY_21_PM = "2026-11-01T14:00:00+09:00"
DAY_22 = "2026-11-02T09:00:00+09:00"
NEXT_RUN = "2026-11-01T08:30:00"


@pytest.fixture
def lab(tmp_path):
    project = tmp_path / "qc_chem.tame"
    shutil.copy(TUTORIAL / "qc_chem.tame", project)
    excel = demo.write_workbook(tmp_path / "qc_chem_daily.xlsx")
    return SimpleNamespace(dir=tmp_path, project=project, excel=excel, result=tmp_path / "qc_chem_result.tame")


def rewrite(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def snapshot(*paths):
    return [path.read_bytes() if path.exists() else None for path in paths]


def result_rows(lab, run=None):
    frame = read_tame(lab.result).df
    return frame if run is None else frame.loc[frame.run.eq(run)]


def test_first_sync_registers_the_whole_source(lab):
    report = sync_project(lab.project, now=DAY_20)
    assert (report.status, report.sequence, report.initial) == ("COMMITTED", 1, True)
    assert len(report.data["added"]) == 40 and not report.data["modified"] and not report.data["removed"]
    assert (report.result["rows"], report.result["new"], report.result["changed"]) == (120, 120, 0)

    project = read_tame(lab.project)
    assert len(project.df) == 40
    first = project.df.iloc[0]
    # Typed spreadsheet cells are stored as plain, reproducible text.
    assert (first["측정일시"], first["Lot"], first["Level"], first["Glucose"], first["Creatinine"]) == ("2026-10-12T08:30:00", "45871", "1", "90", "0.9")
    assert first["비고"] is None
    state = sync_state(project)
    assert (state["SEQUENCE"], state["ROWS"], state["RESULT_ROWS"], state["SYNCED_AT"]) == (1, 40, 120, DAY_20)

    result = result_rows(lab)
    assert len(result) == 120 and set(result.sync_seq) == {"1"} and set(result.sync_status) == {"NEW"}
    assert set(result.synced_at) == {DAY_20}
    # Twenty in-control days: two isolated 1_2s warnings, nothing rejected.
    assert result.decision.value_counts().to_dict() == {"IN_CONTROL": 118, "WARNING": 2}
    warned = result.loc[result.decision.eq("WARNING"), ["result_id", "level", "run", "value"]].values.tolist()
    assert warned == [["glucose", "2", "2026-10-19T08:30:00", "291.0"], ["glucose", "1", "2026-10-31T08:30:00", "95.0"]]


def test_repeating_a_sync_without_changes_writes_nothing(lab):
    sync_project(lab.project, now=DAY_20)
    before = snapshot(lab.project, lab.result)
    report = sync_project(lab.project, now=DAY_21)
    assert (report.status, report.sequence) == ("UNCHANGED", 1)
    assert snapshot(lab.project, lab.result) == before
    assert "변경 없음" in format_report(report)


def test_next_day_one_more_measurement_is_judged_by_the_multirule(lab):
    sync_project(lab.project, now=DAY_20)
    earlier = result_rows(lab).drop(columns=["sync_status"]).reset_index(drop=True)

    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)          # the laboratory adds one row
    report = sync_project(lab.project, now=DAY_21)

    assert (report.status, report.sequence, report.initial) == ("COMMITTED", 2, False)
    assert report.source["file_changed"] and report.source["rows"] == 41
    assert report.data["added"] == [{"key": f"time={NEXT_RUN} | level=1 | instrument=AU5800-1", "row": 42}]
    assert not report.data["modified"] and not report.data["removed"]
    assert {name: report.result[name] for name in ("rows", "new", "changed", "same", "removed")} == {
        "rows": 123, "new": 3, "changed": 0, "same": 120, "removed": 0}

    new = report.new_rows.set_index("result_id")
    assert len(new) == 3 and set(new.run) == {NEXT_RUN} and set(new.level) == {"1"}
    # Glucose: 95 mg/dL against 90 +/- 2 -> z = +2.5, and the previous day was +2.5 as well.
    glucose = new.loc["glucose"]
    assert glucose.z == pytest.approx((95 - 90) / 2)
    assert (glucose.warning, glucose.violation, glucose.rule_scope) == ("1_2s", "2_2s", "1_2s:point; 2_2s:across_run")
    assert (glucose.error_type, glucose.decision, glucose.run_decision) == ("systematic", "REJECT", "REJECT")
    # Creatinine: an unremarkable +0.67 SD, but the tenth consecutive day above the mean.
    creatinine = new.loc["creatinine"]
    assert creatinine.z == pytest.approx((0.92 - 0.90) / 0.03)
    assert (creatinine.warning, creatinine.violation, creatinine.rule_scope) == ("NONE", "10x", "10x:across_run")
    assert (creatinine.error_type, creatinine.decision) == ("systematic", "REJECT")
    # ALT: one value beyond 2 SD with nothing else -> warning only.
    alt = new.loc["alt"]
    assert alt.z == pytest.approx((36 - 32) / 1.5)
    assert (alt.warning, alt.violation, alt.decision, alt.run_decision) == ("1_2s", "NONE", "WARNING", "WARNING")

    result = result_rows(lab)
    added = result.loc[result.sync_seq.eq("2")]
    assert len(added) == 3 and set(added.sync_status) == {"NEW"} and set(added.synced_at) == {DAY_21}
    kept = result.loc[result.sync_seq.eq("1")]
    assert len(kept) == 120 and set(kept.sync_status) == {"SAME"} and set(kept.synced_at) == {DAY_20}
    # Nothing that was already reported has moved.
    pd.testing.assert_frame_equal(kept.drop(columns=["sync_status"]).reset_index(drop=True), earlier)

    project = read_tame(lab.project)
    assert len(project.df) == 41 and project.df.iloc[-1]["측정일시"] == NEXT_RUN
    history = sync_history(project)
    assert history[["SEQUENCE", "ROWS", "ADDED", "RESULT_NEW", "RESULT_CHANGED"]].values.tolist() == [[1, 40, 40, 120, 0], [2, 41, 1, 3, 0]]
    text = format_report(report)
    assert "추가 1 · 수정 0 · 삭제 0" in text and "새 결과 3 · 변경 0" in text and "10x" in text


def test_second_level_of_the_same_run_updates_the_rows_already_reported(lab):
    sync_project(lab.project, now=DAY_20)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(lab.project, now=DAY_21)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_2)
    report = sync_project(lab.project, now=DAY_21_PM)

    assert (report.sequence, report.result["new"], report.result["changed"], report.result["same"]) == (3, 3, 2, 121)
    new = report.new_rows.set_index("result_id")
    assert (new.loc["alt", "violation"], new.loc["alt", "error_type"]) == ("R_4s", "random")     # +2.67 SD and -2.5 SD in one run
    assert new.loc["glucose", "rule_scope"] == "1_2s:point; 2_2s:within_run"
    assert (new.loc["creatinine", "decision"], new.loc["creatinine", "run_decision"]) == ("IN_CONTROL", "REJECT")
    changes = {item["key"].split(" | ")[0]: {change["column"]: (change["old"], change["new"]) for change in item["changes"]}
               for item in report.result["changed_detail"]}
    assert set(changes) == {"result_id=alt", "result_id=glucose"}
    assert changes["result_id=alt"]["decision"] == ("WARNING", "REJECT") and changes["result_id=alt"]["violation"] == ("NONE", "R_4s")
    assert changes["result_id=glucose"] == {"rule_scope": ("1_2s:point; 2_2s:across_run", "1_2s:point; 2_2s:across_run+within_run")}

    run = result_rows(lab, NEXT_RUN)
    assert sorted(zip(run.result_id, run.level, run.sync_seq, run.sync_status)) == [
        ("alt", "1", "2", "CHANGED"), ("alt", "2", "3", "NEW"),
        ("creatinine", "1", "2", "SAME"), ("creatinine", "2", "3", "NEW"),
        ("glucose", "1", "2", "CHANGED"), ("glucose", "2", "3", "NEW")]
    assert set(result_rows(lab).loc[lambda frame: frame.run.ne(NEXT_RUN), "sync_status"]) == {"SAME"}


def test_a_revised_source_row_stops_the_sync_until_it_is_accepted(lab):
    sync_project(lab.project, now=DAY_20)
    before = snapshot(lab.project, lab.result)
    demo.edit_cell(lab.excel, day=5, level="1", column="Glucose", value=97)          # was 92
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)

    blocked = sync_project(lab.project, now=DAY_21)
    assert (blocked.status, blocked.sequence) == ("BLOCKED", 1)
    assert blocked.data["modified"] == [{"key": "time=2026-10-16T08:30:00 | level=1 | instrument=AU5800-1", "row": 10,
                                         "changes": [{"column": "Glucose", "old": "92", "new": "97"}]}]
    assert len(blocked.data["added"]) == 1
    assert snapshot(lab.project, lab.result) == before                                 # nothing was written, not even the new row
    assert "--accept-changes" in format_report(blocked)

    accepted = sync_project(lab.project, now=DAY_21, accept_changes=True)
    assert (accepted.status, accepted.sequence) == ("COMMITTED", 2)
    # 97 mg/dL is +3.5 SD: the run of 16 Oct is now rejected, which changes both of its glucose rows.
    assert (accepted.result["new"], accepted.result["changed"]) == (3, 2)
    revised = result_rows(lab, "2026-10-16T08:30:00").set_index(["result_id", "level"])
    assert (revised.loc[("glucose", "1"), "violation"], revised.loc[("glucose", "1"), "sync_status"]) == ("1_3s", "CHANGED")
    assert (revised.loc[("glucose", "2"), "run_decision"], revised.loc[("glucose", "2"), "sync_status"]) == ("REJECT", "CHANGED")
    entry = log_entries(read_tame(lab.project))[-1]
    assert entry["OPERATION"] == "SYNC" and entry["PARAMS"]["ACCEPTED_REVISIONS"] is True
    assert entry["PARAMS"]["MODIFIED"][0]["changes"] == [{"column": "Glucose", "old": "92", "new": "97"}]
    assert entry["COUNTS"]["ADDED"] == 1 and entry["COUNTS"]["MODIFIED"] == 1


def test_a_removed_source_row_is_a_revision_too(lab):
    sync_project(lab.project, now=DAY_20)
    demo.write_workbook(lab.excel, demo.baseline_rows()[:-1])
    blocked = sync_project(lab.project, now=DAY_21)
    assert blocked.status == "BLOCKED" and blocked.data["removed"] == [{"key": "time=2026-10-31T08:30:00 | level=2 | instrument=AU5800-1"}]
    accepted = sync_project(lab.project, now=DAY_21, accept_changes=True)
    assert (accepted.data["rows_after"], accepted.result["removed"], accepted.result["rows"]) == (39, 3, 117)
    assert len(read_tame(lab.project).df) == 39


def test_check_reports_without_writing(lab):
    sync_project(lab.project, now=DAY_20)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    before = snapshot(lab.project, lab.result)
    checked = sync_project(lab.project, now=DAY_21, dry_run=True)
    assert (checked.status, checked.sequence, checked.result["new"]) == ("CHECKED", 2, 3)
    assert snapshot(lab.project, lab.result) == before
    assert not list(lab.dir.glob(".*sync-tmp*"))
    assert sync_project(lab.project, now=DAY_21).result["new"] == 3


def test_reordering_the_source_is_not_a_change(lab):
    sync_project(lab.project, now=DAY_20)
    demo.write_workbook(lab.excel, list(reversed(demo.baseline_rows())))
    report = sync_project(lab.project, now=DAY_21)
    assert report.status == "UNCHANGED" and report.source["file_changed"]


def test_changed_settings_are_found_recorded_and_applied(lab):
    sync_project(lab.project, now=DAY_20)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(lab.project, now=DAY_21)
    rewrite(lab.project, 'RULES = ["1_3s", "2_2s", "R_4s", "4_1s", "10x"]', 'RULES = ["1_3s", "2_2s", "R_4s", "4_1s"]   # 10x 제외(검토: 2026-11-02)')

    report = sync_project(lab.project, now=DAY_22)
    assert (report.status, report.sequence, report.settings_changed) == ("COMMITTED", 3, True)
    assert not report.data["added"] and (report.result["new"], report.result["changed"]) == (0, 1)
    assert report.result["changed_detail"][0]["key"].startswith("result_id=creatinine")
    creatinine = result_rows(lab, NEXT_RUN).set_index("result_id").loc["creatinine"]
    assert (creatinine.violation, creatinine.decision, creatinine.sync_status, creatinine.sync_seq) == ("NONE", "IN_CONTROL", "CHANGED", "2")

    project = read_tame(lab.project)
    assert [entry["OPERATION"] for entry in log_entries(project)] == ["SYNC", "SYNC", "MANUAL_EDIT", "SYNC"]
    edit = log_entries(project)[2]
    assert edit["PARAMS"]["changed"] == ["CONTROL_SHA256"] and edit["PARAMS"]["before"]["CONTROL_SHA256"] != edit["PARAMS"]["after"]["CONTROL_SHA256"]
    checks = verify_history(project)
    assert checks["errors"] == [] and checks["missing"] == []
    assert [checks["checks"][name] for name in ("history", "data", "controls")] == ["PASS"] * 3
    assert "# 10x 제외(검토: 2026-11-02)" in lab.project.read_text(encoding="utf-8")
    assert sync_project(lab.project, now=DAY_22).status == "UNCHANGED"


def test_hand_written_settings_and_comments_survive_every_sync(lab):
    template = (TUTORIAL / "qc_chem.tame").read_text(encoding="utf-8")
    written_meta = template.split("<META>\n", 1)[1].split("</META>", 1)[0].rstrip()
    sync_project(lab.project, now=DAY_20)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(lab.project, now=DAY_21)
    text = lab.project.read_text(encoding="utf-8")
    meta = text.split("<META>\n", 1)[1].split("</META>", 1)[0]
    assert meta.startswith(written_meta + "\n\n[SYNC_STATE]\n")
    assert meta.count("\n[SYNC_STATE]\n") == 1 and meta.index("\n[SYNC_STATE]\n") < meta.index("\n[[LOG]]\n")
    assert text.split("<DATA>\n", 1)[1].splitlines()[0] == "측정일시\t장비\tLot\tLevel\tGlucose\tCreatinine\tALT\t비고"


def test_splice_falls_back_to_plain_serialization_when_the_text_cannot_be_kept():
    meta = {"INFO": {"NOTE": "x"}, "SYNC_STATE": {"SEQUENCE": 1}}
    text, preserved = splice_managed_meta('# kept\n[INFO]\nNOTE = "x"\n\n[SYNC_STATE]\nSEQUENCE = 0\n', meta)
    assert preserved and text.startswith('# kept\n[INFO]\nNOTE = "x"\n\n[SYNC_STATE]\nSEQUENCE = 1')
    text, preserved = splice_managed_meta('[INFO]\nNOTE = "stale"\n', meta)
    assert not preserved and 'NOTE = "x"' in text


def test_project_and_result_keep_a_verifiable_history(lab):
    sync_project(lab.project, now=DAY_20)
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(lab.project, now=DAY_21)
    for path, operations in ((lab.project, ["SYNC", "SYNC"]),
                             (lab.result, ["SYNC", "SYNC", "WORK:VALIDATE", "PLUGIN_SELECTION_INHERIT", "WORK:WESTGARD_QC", "SYNC:RESULT"])):
        dataset = read_tame(path)
        assert [entry["OPERATION"] for entry in log_entries(dataset)] == operations
        checks = verify_history(dataset)
        assert checks["errors"] == [] and checks["missing"] == []
        assert [checks["checks"][name] for name in ("history", "data", "controls")] == ["PASS"] * 3
    result = read_tame(lab.result)
    assert result.meta["SYNC_RESULT"] == {"PROJECT": "qc_chem.tame", "SOURCE": "qc_chem_daily.xlsx", "WORK": "QC",
                                          "SEQUENCE": 2, "SYNCED_AT": DAY_21}
    plugin = next(entry for entry in log_entries(result) if entry["OPERATION"] == "WORK:WESTGARD_QC")
    assert plugin["EFFECTIVE_PARAMS"]["PLUGIN_IMPLEMENTATION"]["NAME"] == "WESTGARD_QC"
    assert len(plugin["EFFECTIVE_PARAMS"]["PLUGIN_IMPLEMENTATION"]["SOURCE_SHA256"]) == 64
    # Editing the project data outside sync is noticed at the next sync and at audit.
    rewrite(lab.project, "2026-10-12T08:30:00\tAU5800-1\t45871\t1\t90\t", "2026-10-12T08:30:00\tAU5800-1\t45871\t1\t99\t")
    assert verify_history(read_tame(lab.project))["checks"]["data"] == "FAIL"
    report = sync_project(lab.project, now=DAY_22)
    assert report.status == "BLOCKED" and any("sync 밖에서 수정" in warning for warning in report.warnings)
    assert report.data["modified"][0]["changes"] == [{"column": "Glucose", "old": "99", "new": "90"}]


def test_missing_or_edited_result_file_is_rebuilt(lab):
    sync_project(lab.project, now=DAY_20)
    lab.result.unlink()
    rebuilt = sync_project(lab.project, now=DAY_21)
    assert (rebuilt.status, rebuilt.sequence, rebuilt.result["new"]) == ("COMMITTED", 2, 120)
    assert any("결과 파일이 없어" in warning for warning in rebuilt.warnings)
    assert sync_project(lab.project, now=DAY_21).status == "UNCHANGED"

    rewrite(lab.result, "IN_CONTROL", "REJECT")
    repaired = sync_project(lab.project, now=DAY_22)
    assert repaired.status == "COMMITTED" and repaired.result["state"] == "MODIFIED"
    assert (repaired.result["changed"], repaired.result["same"], repaired.result["new"]) == (118, 2, 0)   # every edited row is restored
    assert result_rows(lab).decision.value_counts().to_dict() == {"IN_CONTROL": 118, "WARNING": 2}
    assert sync_history(read_tame(lab.project)).NOTE.tolist()[-1] == "결과 파일을 다시 만들어야 합니다"


def test_failed_analysis_leaves_every_file_untouched(lab):
    sync_project(lab.project, now=DAY_20)
    before = snapshot(lab.project, lab.result)
    demo.append_measurement(lab.excel, {**demo.NEXT_DAY_LEVEL_1, "Lot": "49999"})     # a new lot without a declared baseline
    with pytest.raises(ValueError, match="exactly one applicable baseline"):
        sync_project(lab.project, now=DAY_21)
    assert snapshot(lab.project, lab.result) == before
    assert not list(lab.dir.glob(".*sync-tmp*"))

    baseline = "\n".join(f'[[WESTGARD_QC.BASELINES]]\nRESULT_ID = "{name}"\nLEVEL = "1"\nLOT = "49999"\nINSTRUMENT = "AU5800-1"\n'
                         f'MEAN = {mean}\nSD = {sd}\nUNIT = "{unit}"\nREFERENCE = "New lot, synthetic"\n'
                         'VALID_FROM = "2026-11-01"\nVALID_TO = "2027-04-01"\n'
                         for name, mean, sd, unit in (("glucose", 90.0, 2.0, "mg/dL"), ("creatinine", 0.90, 0.03, "mg/dL"), ("alt", 32.0, 1.5, "U/L")))
    rewrite(lab.project, "</META>", baseline + "</META>")
    report = sync_project(lab.project, now=DAY_21)
    assert (report.status, report.settings_changed, report.result["new"], report.result["changed"]) == ("COMMITTED", True, 3, 0)
    new = report.new_rows.set_index("result_id")
    # A new lot starts new control charts: yesterday's values on the old lot do not complete 2_2s or 10x.
    assert (new.loc["glucose", "violation"], new.loc["glucose", "decision"]) == ("NONE", "WARNING")
    assert new.loc["creatinine", "decision"] == "IN_CONTROL"


def test_result_can_be_an_excel_file(lab):
    rewrite(lab.project, 'RESULT = "qc_chem_result.tame"', 'RESULT = "qc_chem_result.xlsx"')
    workbook = lab.dir / "qc_chem_result.xlsx"
    assert sync_project(lab.project, now=DAY_20).result["new"] == 120
    assert sync_project(lab.project, now=DAY_20).status == "UNCHANGED"              # the stored fingerprint survives the Excel round trip
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    report = sync_project(lab.project, now=DAY_21)
    assert (report.result["new"], report.result["changed"], report.result["same"]) == (3, 0, 120)
    result = read_xlsx(workbook)
    assert len(result.df) == 123 and result.df.sync_status.value_counts().to_dict() == {"SAME": 120, "NEW": 3}
    sheet = openpyxl.load_workbook(workbook)["DATA"]
    assert [cell.value for cell in sheet[1]][-3:] == ["[[NUM]]sync_seq", "[[STR]]synced_at", "[[STR]]sync_status"]
    assert sync_project(lab.project, now=DAY_22).status == "UNCHANGED"


def test_source_layout_problems_are_reported_before_anything_changes(lab):
    sync_project(lab.project, now=DAY_20)
    before = snapshot(lab.project, lab.result)
    rows = demo.baseline_rows()

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = demo.SHEET
    sheet.append([name for name in demo.HEADERS if name != "ALT"])
    workbook.save(lab.excel)
    with pytest.raises(SyncError, match="없는 열: ALT"):
        sync_project(lab.project, now=DAY_21)

    demo.write_workbook(lab.excel, [*rows, rows[-1]])
    with pytest.raises(SyncError, match="같은 키가 중복"):
        sync_project(lab.project, now=DAY_21)

    demo.write_workbook(lab.excel, [*rows, {**demo.NEXT_DAY_LEVEL_1, "Level": None}])
    with pytest.raises(SyncError, match="키 열 'Level' 값이 비어"):
        sync_project(lab.project, now=DAY_21)

    lab.excel.unlink()
    with pytest.raises(SyncError, match="원본 파일을 찾을 수 없습니다"):
        sync_project(lab.project, now=DAY_21)
    assert snapshot(lab.project, lab.result) == before


def test_extra_columns_blank_rows_and_column_order_in_the_source(lab):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = demo.SHEET
    order = ["비고", "ALT", "Creatinine", "Glucose", "Level", "Lot", "장비", "측정일시"]
    sheet.append([*order, "검사자"])
    for row in demo.baseline_rows()[:4]:
        sheet.append([*[row[name] for name in order], "KIM"])
        sheet.append([None] * 9)                                                     # blank spacer rows
    workbook.save(lab.excel)
    report = sync_project(lab.project, now=DAY_20)
    assert report.data["rows_after"] == 4 and report.result["rows"] == 12
    assert any("검사자" in warning for warning in report.warnings)
    assert [item["row"] for item in report.data["added"]] == [2, 4, 6, 8]
    assert list(read_tame(lab.project).df.columns) == demo.HEADERS


def test_append_only_csv_without_keys_or_analysis(tmp_path):
    project = tmp_path / "log.tame"
    project.write_text('<META>\n[SYNC]\nSOURCE = "log.csv"\n\n[COLUMN.sample]\nTAGS = ["STR"]\n\n[COLUMN.value]\nTAGS = ["NUM"]\n</META>\n'
                       "<DATA>\nsample\tvalue\n</DATA>\n", encoding="utf-8")
    source = tmp_path / "log.csv"
    source.write_text("sample,value\nA,1.0\nB,2.5\n", encoding="utf-8")
    first = sync_project(project, now=DAY_20)
    assert (first.status, first.data["mode"], len(first.data["added"])) == ("COMMITTED", "POSITIONAL", 2)
    source.write_text("sample,value\nA,1.00\nB,2.5\nC,3\n", encoding="utf-8")       # 1.00 is the same number as 1.0
    second = sync_project(project, now=DAY_21)
    assert second.data["added"] == [{"key": "row 4", "row": 4}] and not second.data["modified"]
    assert read_tame(project).df.values.tolist() == [["A", "1.00"], ["B", "2.5"], ["C", "3"]]
    source.write_text("sample,value\nA,1.0\nB,9.9\nC,3\n", encoding="utf-8")
    third = sync_project(project, now=DAY_22)
    assert third.status == "BLOCKED" and third.data["modified"][0]["changes"] == [{"column": "value", "old": "2.5", "new": "9.9"}]


def test_cells_the_file_format_normalizes_do_not_look_like_changes(tmp_path):
    project = tmp_path / "notes.tame"
    project.write_text('<META>\n[SYNC]\nSOURCE = "notes.csv"\nKEY_IDS = ["id"]\n\n[COLUMN.id]\nID = "id"\nTAGS = ["STR"]\n\n'
                       '[COLUMN.note]\nTAGS = ["STR", "NULLABLE", "WS_OK"]\n</META>\n<DATA>\nid\tnote\n</DATA>\n', encoding="utf-8")
    source = tmp_path / "notes.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        stream.write('id,note\r\n1,"two\r\nlines"\r\n2,"tab\there, ""quoted"""\r\n3,<<NULL>>\r\n4,   \r\n5,\r\n')
    assert sync_project(project, now=DAY_20).data["rows_after"] == 5
    stored = read_tame(project).df
    assert stored.note.tolist()[:2] == ["two\nlines", 'tab\there, "quoted"'] and str(stored.note[2]) == "<<NULL>>"
    assert stored.note[3] == "   " and stored.note[4] is None
    again = sync_project(project, now=DAY_21)
    assert again.status == "UNCHANGED" and not again.warnings


def test_configuration_errors_are_explained(tmp_path, lab):
    plain = tmp_path / "plain.tame"
    plain.write_text("<META>\n</META>\n<DATA>\na\n1\n</DATA>\n", encoding="utf-8")
    with pytest.raises(SyncError, match=r"META\[SYNC\]"):
        sync_project(plain)
    rewrite(lab.project, 'WORK = "QC" ', 'WORK = "DAILY" ')
    with pytest.raises(SyncError, match=r"META\[WORKS\]"):
        sync_project(lab.project)
    rewrite(lab.project, 'WORK = "DAILY" ', 'WORKFLOW = "QC" ')
    with pytest.raises(SyncError, match="알 수 없는"):
        sync_project(lab.project)


def test_cli_sync(lab, capsys, monkeypatch):
    from tametools.cli import _main

    monkeypatch.chdir(lab.dir)
    assert _main(["sync", str(lab.project)]) == 0
    assert "동기화 완료" in capsys.readouterr().out
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_1)
    assert _main(["sync", str(lab.project), "--check", "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "CHECKED" and len(checked["new_rows"]) == 3
    assert {row["result_id"]: row["decision"] for row in checked["new_rows"]} == {"alt": "WARNING", "creatinine": "REJECT", "glucose": "REJECT"}
    assert _main(["sync", str(lab.project)]) == 0
    assert "새 결과 3 · 변경 0 · 삭제 0 · 유지 120" in capsys.readouterr().out
    demo.edit_cell(lab.excel, day=5, level="1", column="Glucose", value=97)
    assert _main(["sync", str(lab.project)]) == 1
    assert "Glucose: 92 → 97" in capsys.readouterr().out
    assert _main(["sync", str(lab.project), "--accept-changes"]) == 0
    capsys.readouterr()
    assert _main(["sync", str(lab.project), "--history", "--json"]) == 0
    history = json.loads(capsys.readouterr().out)["history"]
    assert [(item["SEQUENCE"], item["ADDED"], item["MODIFIED"], item["RESULT_NEW"], item["RESULT_CHANGED"]) for item in history] == [
        (1, 40, 0, 120, 0), (2, 1, 0, 3, 0), (3, 0, 1, 0, 2)]


def test_two_weeks_of_daily_syncs_stay_small_and_consistent(lab):
    days = 14
    sync_project(lab.project, now=DAY_20)
    sizes = [lab.project.stat().st_size]
    in_control = {"Glucose": (90, 280), "Creatinine": (0.90, 5.20), "ALT": (32, 120)}
    for day in range(1, days + 1):
        moment = demo.FIRST_DAY + dt.timedelta(days=demo.BASELINE_DAYS + day - 1)
        for index, level in enumerate(("1", "2")):
            demo.append_measurement(lab.excel, {"측정일시": moment, "장비": demo.INSTRUMENT, "Lot": demo.LOTS[level], "Level": level,
                                                "비고": None, **{name: values[index] for name, values in in_control.items()}})
        report = sync_project(lab.project, now=(moment + dt.timedelta(minutes=30)).isoformat() + "+09:00")
        assert (report.sequence, len(report.data["added"]), report.result["new"], report.result["changed"]) == (day + 1, 2, 6, 0)
        sizes.append(lab.project.stat().st_size)
    project = read_tame(lab.project)
    assert len(project.df) == 40 + 2 * days and len(sync_history(project)) == days + 1
    result = result_rows(lab)
    assert len(result) == 120 + 6 * days
    assert result.sync_seq.astype(int).value_counts().to_dict() == {1: 120, **{seq: 6 for seq in range(2, days + 2)}}
    assert set(result.decision) == {"IN_CONTROL", "WARNING"}                       # values on the mean never complete a rule
    growth = [after - before for before, after in zip(sizes[1:], sizes[2:])]
    assert max(growth) < 4096        # settings are stored once; each sync adds its rows, one LOG entry and one history line
    assert verify_history(project)["errors"] == []
