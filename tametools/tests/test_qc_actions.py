"""Corrective-action and review records: kept in the project file, joined to the QC report."""
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from tametools.io import read_tame
from tametools.provenance import log_entries
from tametools.provenance_audit import verify_history
from tametools.plugin_base import list_plugins

list_plugins()      # WESTGARD_QC is a plugin package: use its modules as tametools loads them
_qc_actions = importlib.import_module("tametools_dropin_westgard_qc.actions")
action_status = _qc_actions.action_status
add_action = _qc_actions.add_action
add_review = _qc_actions.add_review
effective = _qc_actions.effective
open_rejections = _qc_actions.open_rejections
from tametools.sync import SyncError, rerender_report, sync_project, sync_records, sync_state, verify_records

ROOT = Path(__file__).resolve().parents[2]
TUTORIAL = ROOT / "tutorial" / "26_qc_sync"
_spec = importlib.util.spec_from_file_location("qc_demo_data", TUTORIAL / "qc_demo_data.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

DAY_20, DAY_21, DAY_21_PM, DAY_22 = ("2026-10-31T09:00:00+09:00", "2026-11-01T09:00:00+09:00",
                                     "2026-11-01T14:00:00+09:00", "2026-11-02T09:00:00+09:00")
RUN = "2026-11-01T08:30:00"
ACTED = "2026-11-01T10:12:00+09:00"


@pytest.fixture
def lab(tmp_path):
    """Twenty days synced, then the next day's level-1 row: glucose (2_2s) and creatinine (10x) are rejected."""
    project = tmp_path / "qc_chem.tame"
    shutil.copy(TUTORIAL / "qc_chem.tame", project)
    excel = demo.write_workbook(tmp_path / "qc_chem_daily.xlsx")
    sync_project(project, now=DAY_20)
    demo.append_measurement(excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(project, now=DAY_21)
    return SimpleNamespace(dir=tmp_path, project=project, excel=excel, result=tmp_path / "qc_chem_result.tame",
                           report=tmp_path / "qc_chem_report.html")


def act(lab, **overrides):
    values = dict(run="2026-11-01", results=["Glucose"], cause="시약 lot 교체 후 상승", action="재보정 후 관리물질 재측정",
                  outcome="10:05 재측정 관리 내", patient_impact="보고 전 발견, 재검 없음", by="홍길동", now=ACTED)
    return add_action(lab.project, **{**values, **overrides})


def rewrite(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_rejected_runs_wait_for_an_action_record(lab):
    status = action_status(lab.project)
    assert [(entry["run"], entry["name"], entry["levels"], entry["rules"]) for entry in status["open"]] == [
        (RUN, "Creatinine", ["1"], ["10x"]), (RUN, "Glucose", ["1"], ["2_2s"])]      # ALT only has a warning
    assert status["records"] == [] and status["broken"] == []
    report = sync_project(lab.project, now=DAY_21)
    assert report.status == "UNCHANGED"
    assert report.notices == ["조치 기록이 없는 거부 run 2건 (최근: 2026-11-01 08:30 Glucose). 목록: tametools qc-action PROJECT --list"
                              " · 창으로 기록: tametools qc-action PROJECT --dialog"]
    html = lab.report.read_text(encoding="utf-8")
    assert "<b>조치 기록이 없는 거부 run 2건</b> (전체 거부 2건 중)" in html and html.count("조치 기록 없음</span>") == 2


def test_action_is_kept_in_the_project_and_shown_in_the_report(lab):
    template_meta = (TUTORIAL / "qc_chem.tame").read_text(encoding="utf-8").split("<META>\n", 1)[1].split("</META>", 1)[0].rstrip()
    source, result, state = lab.excel.read_bytes(), lab.result.read_bytes(), sync_state(read_tame(lab.project))

    outcome = act(lab)
    record = outcome["record"]
    assert {key: record[key] for key in ("ID", "KIND", "AT", "BY", "RUN", "RESULTS", "SUPERSEDES", "PREVIOUS_SHA256")} == {
        "ID": 1, "KIND": "ACTION", "AT": ACTED, "BY": "홍길동", "RUN": RUN, "RESULTS": ["glucose"], "SUPERSEDES": 0, "PREVIOUS_SHA256": ""}
    assert (record["CAUSE"], record["ACTION"]) == ("시약 lot 교체 후 상승", "재보정 후 관리물질 재측정")
    assert outcome["report"] == "WRITTEN" and outcome["rejected_in_target"]
    assert [entry["name"] for entry in outcome["open"]] == ["Creatinine"]

    # The data file and the recomputed result are untouched; only the project and the report changed.
    assert lab.excel.read_bytes() == source and lab.result.read_bytes() == result
    project = read_tame(lab.project)
    assert sync_records(project) == [record] and verify_records(sync_records(project)) == []
    assert sync_state(project) == state                                             # recording is not a sync
    text = lab.project.read_text(encoding="utf-8")
    meta = text.split("<META>\n", 1)[1].split("</META>", 1)[0]
    assert meta.startswith(template_meta + "\n\n[SYNC_STATE]\n")                    # hand-written settings stay as written
    assert meta.index("\n[[SYNC_RECORDS.ITEMS]]\n") < meta.index("\n[[LOG]]\n")
    entry = log_entries(project)[-1]
    assert entry["OPERATION"] == "SYNC:RECORD" and entry["PARAMS"]["SHA256"] == record["SHA256"]
    checks = verify_history(project)
    assert checks["errors"] == [] and checks["missing"] == []
    assert [checks["checks"][name] for name in ("history", "data", "controls")] == ["PASS"] * 3

    html = lab.report.read_text(encoding="utf-8")
    assert "<span>조치</span>재보정 후 관리물질 재측정<br><span>원인</span>시약 lot 교체 후 상승" in html
    assert "<span>조치 후</span>10:05 재측정 관리 내<br><span>환자 결과 영향</span>보고 전 발견, 재검 없음" in html
    assert "#1 · 홍길동 · 2026-11-01 10:12" in html and "<b>조치 기록이 없는 거부 run 1건</b>" in html
    assert html.count("조치 기록 없음</span>") == 1                                    # creatinine is still waiting
    assert "동기화 <b>#2 (2026-11-01 09:00)</b>" in html and "작성 <b>2026-11-01 10:12</b>" in html
    assert "새 결과 중 거부</div><div class='value'>2</div>" in html                   # the last sync is still described

    # A later sync has nothing to redo and still reminds about the open rejection.
    again = sync_project(lab.project, now=DAY_22)
    assert again.status == "UNCHANGED" and len(again.notices) == 1 and "거부 run 1건" in again.notices[0]


def test_records_follow_the_results_through_later_syncs(lab):
    act(lab, results=[], cause="정도관리물질 교체 직후", action="새 바이알로 재측정")          # no --result: the whole run
    assert action_status(lab.project)["open"] == []
    demo.append_measurement(lab.excel, demo.NEXT_DAY_LEVEL_2)                         # ALT becomes R_4s: a new rejection in the same run
    report = sync_project(lab.project, now=DAY_21_PM)
    assert report.status == "COMMITTED" and report.notices == []                      # ... which the whole-run record already covers
    assert len(sync_records(read_tame(lab.project))) == 1
    html = lab.report.read_text(encoding="utf-8")
    assert "거부된 run 3건 모두 조치 기록이 있습니다." in html and "조치 기록 없음</span>" not in html
    assert html.count("#1 조치 기록과 같음") == 4 and html.count("<span>조치</span>새 바이알로 재측정") == 1

    # The corrected value of 16 Oct (accepted revision) makes that run a rejection: it shows up as open.
    demo.edit_cell(lab.excel, day=5, level="1", column="Glucose", value=97)
    later = sync_project(lab.project, now=DAY_22, accept_changes=True)
    assert later.notices and "거부 run 1건 (최근: 2026-10-16 08:30 Glucose)" in later.notices[0]
    assert [entry["run"] for entry in action_status(lab.project)["open"]] == ["2026-10-16T08:30:00"]


def test_a_record_is_corrected_by_a_new_record_never_edited(lab):
    first = act(lab)["record"]
    second = act(lab, run="", results=None, supersedes=1, cause="보정물질 lot 변경", action="재보정", now=DAY_21_PM)["record"]
    assert (second["ID"], second["SUPERSEDES"], second["RUN"], second["RESULTS"]) == (2, 1, RUN, ["glucose"])   # the target is kept
    assert second["PREVIOUS_SHA256"] == first["SHA256"]
    records = sync_records(read_tame(lab.project))
    assert [record["ID"] for record in records] == [1, 2] and [record["ID"] for record in effective(records, "ACTION")] == [2]
    html = lab.report.read_text(encoding="utf-8")
    assert "<span>원인</span>보정물질 lot 변경" in html and "시약 lot 교체 후 상승" not in html
    assert "#2 · 홍길동 · 2026-11-01 14:00 · #1 을(를) 정정" in html
    with pytest.raises(SyncError, match="이미 다른 기록으로 정정"):
        act(lab, supersedes=1)
    with pytest.raises(SyncError, match="없거나 종류가 다릅니다"):
        add_review(lab.project, start="2026-10-12", end="2026-11-01", by="김책임", supersedes=2)
    assert len(sync_records(read_tame(lab.project))) == 2


def test_editing_a_record_outside_the_tool_is_noticed(lab):
    act(lab)
    act(lab, results=["creatinine"], cause="원인 미상", action="관찰", now=DAY_21_PM)
    rewrite(lab.project, 'ACTION = "재보정 후 관리물질 재측정"', 'ACTION = "조치 없음"')
    records = sync_records(read_tame(lab.project))
    assert verify_records(records) == [1] and action_status(lab.project)["broken"] == [1]
    report = sync_project(lab.project, now=DAY_22)
    assert any("도구 밖에서 수정되었거나 빠진 것이 있습니다: #1" in warning for warning in report.warnings)
    assert rerender_report(lab.project, now=DAY_22) == "WRITTEN"
    assert "<b>기록 확인 필요</b>: 프로젝트에 남긴 기록 #1 이(가)" in lab.report.read_text(encoding="utf-8")


def test_review_records_who_reviewed_which_period(lab):
    act(lab)
    outcome = add_review(lab.project, start="2026-10-12", end="2026-11-01", by="김책임", note="Creatinine 추세 관찰", now=DAY_22)
    record = outcome["record"]
    assert {key: record[key] for key in ("ID", "KIND", "FROM", "TO", "BY", "RUNS", "REJECTED_RUNS", "OPEN_AT_REVIEW")} == {
        "ID": 2, "KIND": "REVIEW", "FROM": "2026-10-12", "TO": "2026-11-01", "BY": "김책임", "RUNS": 21, "REJECTED_RUNS": 2, "OPEN_AT_REVIEW": 1}
    assert [entry["name"] for entry in outcome["open_in_period"]] == ["Creatinine"]
    html = lab.report.read_text(encoding="utf-8")
    assert "7. 검토 기록</h2>" in html and "2026-10-12 ~ 2026-11-01</td><td class=''>김책임</td>" in html
    assert "이 보고서의 기간(2026-10-12 ~ 2026-11-01)은 위 검토 기록에 포함되어 있습니다." in html and "<div class='sign'>" not in html
    # A review that does not reach the last day leaves the period open.
    add_review(lab.project, start="2026-10-12", end="2026-10-31", by="김책임", supersedes=2, now=DAY_22)
    assert "전체를 포함하는 검토 기록이 아직 없습니다" in lab.report.read_text(encoding="utf-8")
    for bad, message in (({"start": "2026-11-02", "end": "2026-11-01"}, "시작일이 종료일보다"), ({"start": "11/01", "end": "2026-11-01"}, "YYYY-MM-DD")):
        with pytest.raises(SyncError, match=message):
            add_review(lab.project, by="김책임", **bad)


@pytest.mark.parametrize("overrides, message", [
    ({"cause": ""}, "원인.*조치 내용"),
    ({"action": " "}, "원인.*조치 내용"),
    ({"by": ""}, "기록자"),
    ({"run": ""}, "--run"),
    ({"run": "2026-12-01"}, "run '2026-12-01' 이\\(가\\) 없습니다"),
    ({"run": "2026-10"}, "시작하는 run 이 여러 개"),
    ({"results": ["sodium"]}, "검사 'sodium' 이\\(가\\) 없습니다. 있는 검사: alt, creatinine, glucose"),
    ({"instrument": "AU-9"}, "장비 'AU-9' 의 결과가 없습니다"),
])
def test_incomplete_or_mistargeted_records_are_refused(lab, overrides, message):
    before = lab.project.read_bytes()
    with pytest.raises(SyncError, match=message):
        act(lab, **overrides)
    assert lab.project.read_bytes() == before


def test_records_need_a_synced_and_unedited_project(lab, tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    shutil.copy(TUTORIAL / "qc_chem.tame", fresh / "qc_chem.tame")
    demo.write_workbook(fresh / "qc_chem_daily.xlsx")
    with pytest.raises(SyncError, match="결과 파일이 없습니다"):
        add_action(fresh / "qc_chem.tame", run="2026-10-12", cause="x", action="y", by="z")

    rewrite(lab.project, 'RULES = ["1_3s", "2_2s", "R_4s", "4_1s", "10x"]', 'RULES = ["1_3s", "2_2s", "R_4s", "4_1s"]')
    with pytest.raises(SyncError, match="직접 수정되었습니다. 먼저 tametools sync"):
        act(lab)
    sync_project(lab.project, now=DAY_22)                                             # the settings change is recorded first
    assert act(lab)["record"]["ID"] == 1

    lab.result.unlink()
    with pytest.raises(SyncError, match="결과 파일이 없습니다"):
        act(lab, results=["creatinine"])


def test_a_note_on_a_run_that_is_not_rejected_is_kept_and_listed_apart(lab):
    outcome = act(lab, run="2026-10-19", results=["glucose"], cause="1_2s 경고", action="추가 조치 없이 관찰")
    assert outcome["rejected_in_target"] is False and len(outcome["open"]) == 2
    html = lab.report.read_text(encoding="utf-8")
    assert "<span>조치</span>추가 조치 없이 관찰" in html                              # shown on the warning row of 19 Oct
    act(lab, run="2026-10-12", results=[], cause="정기 점검", action="관리물질 새 바이알 개봉", now=DAY_21_PM)
    assert "이 목록에 없는 run 의 조치 기록" in lab.report.read_text(encoding="utf-8")


def test_cli_records_actions_and_reviews(lab, capsys, monkeypatch):
    from tametools.cli import _main

    monkeypatch.chdir(lab.dir)
    assert _main(["qc-action", str(lab.project), "--list"]) == 0
    listing = capsys.readouterr().out
    assert "조치 기록이 없는 거부 run 2건 (전체 거부 run 2건)" in listing and f"{RUN}  Glucose  수준 1  2_2s" in listing
    assert _main(["qc-action", str(lab.project), "--run", "2026-11-01", "--result", "Glucose,creatinine", "--cause", "원인 미상",
                  "--action", "재보정", "--by", "홍길동"]) == 0
    added = capsys.readouterr().out
    assert "조치 기록 #1 을(를) 남겼습니다" in added and "보고서: 갱신했습니다" in added and "거부 run: 0건" in added
    assert _main(["qc-action", str(lab.project), "--supersedes", "1", "--cause", "보정 편차", "--action", "재보정", "--by", "홍길동", "--json"]) == 0
    corrected = json.loads(capsys.readouterr().out)
    assert corrected["record"]["ID"] == 2 and corrected["record"]["RESULTS"] == ["glucose", "creatinine"] and corrected["open"] == []
    assert _main(["qc-action", str(lab.project), "--list"]) == 0
    assert "조치 기록 2건 (정정으로 대체된 기록 1건 포함)" in capsys.readouterr().out
    assert _main(["qc-review", str(lab.project), "--from", "2026-10-12", "--to", "2026-11-01", "--by", "김책임"]) == 0
    assert "기간 내 run 21개, 거부 run 2건, 조치 기록이 없는 거부 run 0건" in capsys.readouterr().out
    assert _main(["qc-review", str(lab.project), "--list", "--json"]) == 0
    assert [record["BY"] for record in json.loads(capsys.readouterr().out)["records"]] == ["김책임"]
    assert _main(["sync", str(lab.project), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["notices"] == []
    with pytest.raises(SyncError, match="기록자"):
        _main(["qc-action", str(lab.project), "--run", "2026-11-01", "--cause", "x", "--action", "y"])


def test_open_rejections_ignore_superseded_and_unrelated_records():
    import pandas as pd
    result = pd.DataFrame([{"result_id": "glucose", "instrument": "A", "run": "r1", "run_decision": "REJECT", "violation": "1_3s",
                            "level": "1", "time": "2026-01-01T08:00:00+09:00"},
                           {"result_id": "alt", "instrument": "A", "run": "r1", "run_decision": "IN_CONTROL", "violation": "NONE",
                            "level": "1", "time": "2026-01-01T08:00:00+09:00"}])
    record = {"ID": 1, "KIND": "ACTION", "RUN": "r1", "INSTRUMENT": "", "RESULTS": ["glucose"], "SUPERSEDES": 0}
    assert len(open_rejections(result, [])) == 1 and open_rejections(result, [record]) == []
    assert len(open_rejections(result, [{**record, "RESULTS": ["alt"]}])) == 1                       # about another analyte
    assert len(open_rejections(result, [{**record, "INSTRUMENT": "B"}])) == 1                        # about another instrument
    assert len(open_rejections(result, [record, {**record, "ID": 2, "RUN": "r2", "SUPERSEDES": 1}])) == 1   # corrected to another run
    assert open_rejections(result, [{"ID": 1, "KIND": "REVIEW", "FROM": "2026-01-01", "TO": "2026-01-31"}]) != []
