"""Questions put to the person running a plugin or command: one declaration, shown as a window, as terminal prompts, or not at all."""
import importlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import sys
import threading
from types import SimpleNamespace

import pytest

from tametools import dialogs
from tametools.cli import _main
from tametools.dialogs import DialogUnavailable, check_answers, field, form
from tametools.io import read_tame
from tametools.models import OperationOutput
from tametools.plugin_base.base import PLUGIN_REGISTRY, register_plugin
from tametools.plugin_base import list_plugins

list_plugins()      # WESTGARD_QC is a plugin package: use its modules as tametools loads them
_qc_actions = importlib.import_module("tametools_dropin_westgard_qc.actions")
action_status = _qc_actions.action_status
add_action = _qc_actions.add_action
from tametools.sync import sync_project, sync_records

ROOT = Path(__file__).resolve().parents[2]
TUTORIAL = ROOT / "tutorial" / "26_qc_sync"
_spec = importlib.util.spec_from_file_location("qc_demo_data", TUTORIAL / "qc_demo_data.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

RECORD = form("QC 조치 기록", [
    field("cause", "원인", required=True, hint="모르면 '원인 미상'"),
    field("action", "조치 내용", "multiline", required=True),
    field("impact", "환자 결과 영향", "choice", choices=[("NONE", "영향 없음"), ("RETEST", "재검")]),
    field("rules", "규칙", "multi", choices=["1_3s", "2_2s", "R_4s"], default=["1_3s"]),
    field("repeat", "재측정 완료", "check"),
    field("count", "재측정 횟수", "integer", minimum=0, maximum=9),
    field("day", "조치일", "date"),
    field("by", "기록자", required=True),
], intro="2026-11-01 run · Glucose", submit="저장")
TYPED = {"cause": "시약 lot 교체", "action": "재보정\n관리물질 재측정", "impact": "재검", "rules": ["1_3s", "R_4s"], "repeat": True,
         "count": "2", "day": "2026-11-01", "by": "홍길동"}
ACCEPTED = {**TYPED, "impact": "RETEST", "count": 2}
TAME = ("<META>\n[INFO]\nDESCRIPTION = \"dialog test\"\n\n[COLUMN.\"보고값\"]\nID = \"result_value\"\n</META>\n<DATA>\n"
        "[[TESTNAME]]검사항목명\t[[SEX]]성별\t[[AGE]]나이\t[[RESULT::<NUM>]]보고값\n"
        "AST\tM\t5\t10\nAST\tF\t15\t12\nALT\tM\t35\t30\nALT\tF\t82\t40\n</DATA>\n")


REAL_PRESENCE = dialogs.someone_is_there       # the fixture below replaces it for every test


def window_possible() -> bool:
    """Decided without opening one: a Tk root opened while pytest collects makes the first root inside a test fail."""
    if importlib.util.find_spec("tkinter") is None:
        return False
    return not sys.platform.startswith("linux") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


needs_window = pytest.mark.skipif(not window_possible(), reason="no display or no tkinter in this environment")


@pytest.fixture(autouse=True)
def unattended(monkeypatch):
    """Tests are an unattended run unless one says otherwise; no test may open a window on the screen or wait at a terminal."""
    monkeypatch.delenv(dialogs.ENVIRONMENT, raising=False)
    monkeypatch.setattr(dialogs, "someone_is_there", lambda: False)
    monkeypatch.setattr(dialogs, "_ask_gui", lambda made, start: (_ for _ in ()).throw(DialogUnavailable("화면이 없는 환경입니다")))


def answering(monkeypatch, typed, seen=None):
    """A person at a dialog window who types ``typed`` (None: presses cancel)."""
    def window(made, start):
        if seen is not None:
            seen.append((made, dict(start)))
        if typed is None:
            return None
        answers, errors = check_answers(made, {**start, **typed})
        assert not errors, errors
        return answers
    monkeypatch.setattr(dialogs, "_ask_gui", window)


def prompts(lines):
    """A terminal: the typed lines in order, and everything that was written to it."""
    waiting, written = list(lines), []

    def read(prompt):
        written.append(prompt)
        if not waiting:
            raise EOFError
        return waiting.pop(0)
    return read, written.append, written


# --- the declaration ---------------------------------------------------------------------------------------------------

def test_a_declaration_is_checked_when_it_is_written():
    for wrong, message in [(lambda: field(""), "needs a key"), (lambda: field("a", kind="slider"), "unknown kind"),
                           (lambda: field("a", kind="choice"), "need at least one"), (lambda: field("a", choices=["x"]), "choices belong"),
                           (lambda: field("a", kind="choice", choices=[(1, "x"), (2, "x")]), "same text"),
                           (lambda: field("a", tags=["RESULT"]), "tags and value belong"),
                           (lambda: field("a", kind="column", value="header"), "'name' or 'id'"),
                           (lambda: form("t", [field("a"), field("A")]), "used twice")]:
        with pytest.raises(ValueError, match=message):
            wrong()
    with pytest.raises(TypeError):
        form("t", ["cause"])


def test_answers_come_back_typed_and_checked():
    answers, errors = check_answers(RECORD, TYPED)
    assert (answers, errors) == (ACCEPTED, {})
    answers, errors = check_answers(RECORD, {"cause": "  ", "action": "a", "impact": "폐기", "rules": "1_3s, 9x", "count": "12",
                                             "day": "2026/11/01", "by": "홍길동"})
    assert errors == {"cause": "'원인' 은(는) 필수 항목입니다", "impact": "'환자 결과 영향': 고를 수 없는 값입니다: 폐기",
                      "rules": "'규칙': 고를 수 없는 값입니다: 9x", "count": "'재측정 횟수' 은(는) 9 이하여야 합니다",
                      "day": "'조치일' 은(는) YYYY-MM-DD 형식의 날짜여야 합니다: 2026/11/01"}
    assert answers == {"action": "a", "repeat": False, "by": "홍길동"}
    assert check_answers(RECORD, {**TYPED, "count": "1.5"})[1] == {"count": "'재측정 횟수' 에는 정수를 적어 주세요: 1.5"}
    numbers = form("t", [field("x", kind="number"), field("n", kind="number")])
    assert check_answers(numbers, {"x": "0.25", "n": "120"})[0] == {"x": 0.25, "n": 120}       # a whole number stays whole
    assert check_answers(numbers, {"x": "nan", "n": True})[1].keys() == {"x", "n"}


def test_current_options_are_the_starting_values_and_empty_answers_remove_them():
    options = {"CAUSE": "앞서 적은 원인", "rules": "2_2s,R_4s", "Repeat": "true", "OTHER": 1}
    assert dialogs.initial(RECORD, options) == {"cause": "앞서 적은 원인", "action": "", "impact": "", "rules": ["2_2s", "R_4s"],
                                                 "repeat": True, "count": "", "day": "", "by": ""}
    assert [item.key for item in dialogs.missing(RECORD, options)] == ["action", "by"]
    assert dialogs.missing(RECORD, {**options, "ACTION": "a", "BY": "b"}) == []
    answers = {**ACCEPTED, "cause": "새 원인", "rules": [], "count": None, "day": ""}
    assert dialogs.merged(RECORD, options, answers) == {
        "OTHER": 1, "cause": "새 원인", "action": ACCEPTED["action"], "impact": "RETEST", "repeat": True, "by": "홍길동"}
    assert dialogs.option_text(RECORD, answers) == ["cause=새 원인", "action=재보정\n관리물질 재측정", "impact=RETEST", "repeat=true", "by=홍길동"]


def test_a_current_value_outside_the_choices_is_not_lost_by_opening_and_saving(monkeypatch):
    seen = []
    answering(monkeypatch, {"cause": "c", "action": "a", "by": "b"}, seen)
    answers = dialogs.ask(RECORD, {"impact": "HOLD", "rules": ["1_3s", "10x"]})
    assert (answers["impact"], answers["rules"]) == ("HOLD", ["1_3s", "10x"])
    shown = {item.key: [text for _, text in item.choices] for item in seen[0][0].fields if item.choices}
    assert shown == {"impact": ["영향 없음", "재검", "HOLD"], "rules": ["1_3s", "2_2s", "R_4s", "10x"]}
    comma = form("t", [field("target", kind="choice", choices=["11-01 · Creatinine, Glucose", "11-01 · Glucose"], default="11-01 · Creatinine, Glucose")])
    dialogs.ask(comma)                                              # a choice whose text has a comma is one choice, not two
    assert len(seen[1][0].fields[0].choices) == 2


def test_column_questions_offer_the_columns_of_the_dataset(tmp_path):
    path = tmp_path / "data.tame"
    path.write_text(TAME, encoding="utf-8")
    dataset = read_tame(path)
    made = dialogs.resolved(form("t", [field("GROUP", "묶을 열", "column"), field("RESULTS", "결과 열", "columns", tags=["RESULT"]),
                                       field("RESULT_ID", "결과", "column", value="id")]), dataset)
    assert [(item.kind, [value for value, _ in item.choices]) for item in made.fields] == [
        ("choice", ["검사항목명", "성별", "나이", "보고값"]), ("multi", ["보고값"]), ("choice", ["result_value"])]
    assert made.fields[2].choices == (("result_value", "보고값 (result_value)"),)
    with pytest.raises(ValueError, match="needs the dataset"):
        dialogs.resolved(form("t", [field("GROUP", kind="column")]))
    with pytest.raises(ValueError, match="고를 수 있는 열 \\(QC 태그\\)이 자료에 없습니다"):
        dialogs.resolved(form("t", [field("GROUP", "묶을 열", "column", tags=["QC"])]), dataset)


# --- who is asked ------------------------------------------------------------------------------------------------------

def test_left_to_itself_the_program_asks_only_a_person_and_only_for_what_is_missing(monkeypatch):
    assert not dialogs.wanted(None, needed=True)                    # a pipe, a scheduler, a test: never
    assert dialogs.wanted(True, needed=False) and not dialogs.wanted(False, needed=True)
    monkeypatch.setattr(dialogs, "someone_is_there", lambda: True)
    assert dialogs.wanted(None, needed=True) and not dialogs.wanted(None, needed=False)
    monkeypatch.setenv(dialogs.ENVIRONMENT, "off")
    assert not dialogs.wanted(None, needed=True) and dialogs.wanted(True, needed=True)     # the command line outranks the environment


def test_an_empty_input_device_is_not_a_person(monkeypatch):
    with open(os.devnull) as nothing:                               # what a scheduled task has as stdin; on Windows it says isatty()
        monkeypatch.setattr(sys, "stdin", nothing)
        assert REAL_PRESENCE() is False
    for stream in (io.StringIO("y\n"), None):                       # a pipe; no stdin at all (a windowed program)
        monkeypatch.setattr(sys, "stdin", stream)
        assert REAL_PRESENCE() is False


def test_an_unattended_run_names_what_it_needs_instead_of_waiting():
    with pytest.raises(DialogUnavailable) as caught:
        dialogs.ask(RECORD, {"cause": "c"})
    message = str(caught.value)
    assert "화면이 없는 환경입니다" in message and "터미널에서 실행한 것이 아니어서" in message
    assert "action (조치 내용), by (기록자)" in message and "cause" not in message


def test_without_a_screen_the_same_questions_are_put_in_the_terminal(monkeypatch):
    monkeypatch.setattr(dialogs, "someone_is_there", lambda: True)
    asked = []
    monkeypatch.setattr(dialogs, "_ask_terminal", lambda made, start: asked.append(made.title) or {"via": "terminal"})
    assert dialogs.ask(RECORD) == {"via": "terminal"}               # the window could not open
    answering(monkeypatch, None)
    assert dialogs.ask(RECORD) is None and asked == ["QC 조치 기록"]  # a window opened: the terminal is not used
    monkeypatch.setenv(dialogs.ENVIRONMENT, "terminal")
    assert dialogs.ask(RECORD) == {"via": "terminal"}               # asked for by the environment


def test_terminal_prompts_keep_asking_until_the_answer_can_be_accepted():
    read, write, written = prompts(["\ufeff", "시약 lot 교체", "재보정", "3", "2", "1,3", "예", "x", "2", "", "홍길동"])
    answers = dialogs._ask_terminal(RECORD, dialogs.initial(RECORD, {"day": "2026-11-01"}), read=read, write=write)
    assert answers == {"cause": "시약 lot 교체", "action": "재보정", "impact": "RETEST", "rules": ["1_3s", "R_4s"], "repeat": True,
                       "count": 2, "day": "2026-11-01", "by": "홍길동"}
    text = "\n".join(written)
    assert written[:3] == ["[QC 조치 기록]", "2026-11-01 run · Glucose", "(Enter: 괄호 안의 값 그대로 · -: 비움)"]
    assert "   '원인' 은(는) 필수 항목입니다" in text and "   '환자 결과 영향': 고를 수 없는 값입니다: 3" in text
    assert "   '재측정 횟수' 에는 정수를 적어 주세요: x" in text
    assert "   1) 영향 없음\n   2) 재검\n환자 결과 영향: " in text and "규칙 (번호를 쉼표로) [1_3s]: " in text
    assert "원인 * (모르면 '원인 미상'): " in text and "조치일 (YYYY-MM-DD) [2026-11-01]: " in text and "재측정 완료 (y/n) [n]: " in text


def test_terminal_answers_can_be_cleared_and_the_end_of_input_cancels():
    read, write, _ = prompts(["c", "a", "-", "-", "", "", "-", "b"])
    answers = dialogs._ask_terminal(RECORD, dialogs.initial(RECORD, {"impact": "RETEST", "day": "2026-11-01"}), read=read, write=write)
    assert (answers["impact"], answers["rules"], answers["day"], answers["repeat"]) == ("", [], "", False)
    read, write, _ = prompts(["c", "a"])
    assert dialogs._ask_terminal(RECORD, dialogs.initial(RECORD), read=read, write=write) is None


def test_terminal_questions_go_to_stderr_so_that_stdout_stays_the_output_of_the_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("시약 lot 교체\r\n홍길동\n"))
    short = form("QC 조치 기록", [field("cause", "원인", required=True), field("by", "기록자", required=True)])
    assert dialogs._ask_terminal(short, dialogs.initial(short)) == {"cause": "시약 lot 교체", "by": "홍길동"}
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == "[QC 조치 기록]\n원인 *: 기록자 *: "
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))              # input ended: cancelled, not an endless question
    assert dialogs._ask_terminal(short, dialogs.initial(short)) is None


def test_a_form_without_fields_is_a_confirmation(monkeypatch):
    question = form("확인", intro="이 run 은 거부 상태가 아닙니다. 그래도 기록할까요?", submit="기록")
    for typed, expected in (("y", {}), ("n", None)):
        read, write, written = prompts([typed])
        assert dialogs._ask_terminal(question, {}, read=read, write=write) == expected
        assert written[-1] == "기록? (y/n): "
    answering(monkeypatch, {})
    assert dialogs.confirm("계속할까요?") is True
    answering(monkeypatch, None)
    assert dialogs.confirm("계속할까요?") is False


# --- the window --------------------------------------------------------------------------------------------------------

@needs_window
def test_the_window_returns_what_was_typed_and_refuses_an_incomplete_form(monkeypatch):
    monkeypatch.undo()                                              # the real window, driven by a script and never shown
    seen = {}

    def person(window):
        seen["start"] = {item.key: window.get(item.key) for item in RECORD.fields}
        window.submit()
        seen["refused"] = window.error
        for key, value in TYPED.items():
            window.set(key, value)
        seen["typed"] = {item.key: window.get(item.key) for item in RECORD.fields}
        window.submit()

    start = dialogs.initial(RECORD, {"day": "2026-11-01", "impact": "NONE"})
    assert dialogs._ask_gui(RECORD, start, _script=person) == ACCEPTED
    assert seen["start"] == {"cause": "", "action": "", "impact": "NONE", "rules": ["1_3s"], "repeat": False, "count": "",
                             "day": "2026-11-01", "by": ""}
    assert seen["refused"] == "'원인' 은(는) 필수 항목입니다"
    assert seen["typed"] == {**TYPED, "impact": "RETEST"}


@needs_window
def test_closing_the_window_cancels_and_a_stuck_script_cannot_hang_the_run(monkeypatch):
    monkeypatch.undo()
    assert dialogs._ask_gui(RECORD, dialogs.initial(RECORD), _script=lambda window: window.cancel()) is None
    assert dialogs._ask_gui(RECORD, dialogs.initial(RECORD), _script=lambda window: window.submit()) is None   # refused, then closed
    assert dialogs._ask_gui(form("확인", intro="계속할까요?"), {}, _script=lambda window: window.submit()) == {}


@needs_window
def test_a_window_is_opened_only_from_the_thread_that_started_the_program(monkeypatch):
    monkeypatch.undo()
    caught = []

    def work():
        try:
            dialogs._ask_gui(RECORD, dialogs.initial(RECORD), _script=lambda window: window.cancel())
        except DialogUnavailable as exc:
            caught.append(str(exc))

    worker = threading.Thread(target=work)
    worker.start()
    worker.join(30)
    assert caught == ["창은 프로그램을 시작한 스레드에서만 엽니다"]


# --- plugins -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def probe(tmp_path):
    """A plugin that declares its questions, and a small file to run it on."""
    received = []
    questions = form("분석 설정", [
        field("GROUP", "묶을 열", "column", required=True),
        field("MIN_N", "최소 자료 수", "integer", default=2, minimum=1),
        field("MODE", "방식", "choice", choices=[("MEAN", "평균"), ("MEDIAN", "중앙값")], default="MEAN"),
    ])

    @register_plugin("DIALOG_PROBE", description="test plugin with declared questions", form=questions)
    def handler(dataset, meta, step_name, options):
        received.append(dict(options))
        return OperationOutput(name=step_name, dataset=dataset, message="probe ran")

    @register_plugin("DIALOG_INSIDE", description="test plugin that asks while it computes")
    def impatient(dataset, meta, step_name, options):
        dialogs.ask(questions, dataset=dataset)

    path = tmp_path / "data.tame"
    path.write_text(TAME, encoding="utf-8")
    yield SimpleNamespace(path=path, received=received, form=questions, spec=PLUGIN_REGISTRY["DIALOG_PROBE"], out=tmp_path / "out.tame")
    PLUGIN_REGISTRY.pop("DIALOG_PROBE", None)
    PLUGIN_REGISTRY.pop("DIALOG_INSIDE", None)


def run(probe, *options):
    return _main(["run-plugin", str(probe.path), "DIALOG_PROBE", "--allow-plugins", *options])


def test_a_plugin_run_asks_for_the_missing_setting_and_runs_with_the_answer(probe, monkeypatch, capsys):
    monkeypatch.setattr(dialogs, "someone_is_there", lambda: True)
    seen = []
    answering(monkeypatch, {"GROUP": "검사항목명", "MIN_N": "3"}, seen)
    assert run(probe, "--output", str(probe.out)) == 0
    assert probe.received == [{"GROUP": "검사항목명", "MIN_N": 3, "MODE": "MEAN"}]
    made, start = seen[0]
    assert made.title == "분석 설정" and start == {"GROUP": "", "MIN_N": 2, "MODE": "MEAN"}
    assert [text for _, text in made.fields[0].choices] == ["검사항목명", "성별", "나이", "보고값"]
    printed = capsys.readouterr().out
    assert "answered: GROUP=검사항목명, MIN_N=3, MODE=MEAN" in printed
    assert "repeat without asking: --option GROUP=검사항목명 --option MIN_N=3 --option MODE=MEAN" in printed
    logged = probe.out.read_text(encoding="utf-8")                  # what was answered is in the log like any other option
    assert "PLUGIN_REQUESTED_OPTIONS" in logged and "검사항목명" in logged.split("PLUGIN_REQUESTED_OPTIONS", 1)[1]


def test_a_complete_command_line_is_not_interrupted_and_dialog_shows_the_current_settings(probe, monkeypatch, capsys):
    monkeypatch.setattr(dialogs, "someone_is_there", lambda: True)
    seen = []
    answering(monkeypatch, {"MODE": "중앙값"}, seen)
    assert run(probe, "--option", "GROUP=성별") == 0
    assert seen == [] and probe.received == [{"GROUP": "성별"}] and "answered" not in capsys.readouterr().out
    assert run(probe, "--option", "GROUP=성별", "--dialog") == 0
    assert seen[0][1] == {"GROUP": "성별", "MIN_N": 2, "MODE": "MEAN"}
    assert probe.received[1] == {"GROUP": "성별", "MIN_N": 2, "MODE": "MEDIAN"}


def test_an_unattended_plugin_run_stops_with_the_names_of_the_missing_settings(probe, capsys):
    with pytest.raises(DialogUnavailable, match="필요한 설정이 지정되지 않았습니다: GROUP \\(묶을 열\\). --option KEY=VALUE"):
        run(probe)
    with pytest.raises(DialogUnavailable):
        run(probe, "--no-dialog")
    assert probe.received == []
    assert run(probe, "--option", "GROUP=나이", "--no-dialog") == 0 and probe.received == [{"GROUP": "나이"}]


def test_cancelling_the_questions_does_not_run_the_plugin(probe, monkeypatch, capsys):
    answering(monkeypatch, None)
    assert run(probe, "--dialog") == 1
    assert probe.received == [] and "cancelled: the plugin was not run." in capsys.readouterr().out


def test_nothing_is_asked_while_a_plugin_computes(probe, monkeypatch):
    answering(monkeypatch, {"GROUP": "성별"})
    with pytest.raises(DialogUnavailable, match="DIALOG_INSIDE: 플러그인이 계산하는 동안에는 물을 수 없습니다"):
        _main(["run-plugin", str(probe.path), "DIALOG_INSIDE", "--allow-plugins"])
    assert dialogs.ask(probe.form, dataset=read_tame(probe.path))["GROUP"] == "성별"      # outside a run it is allowed again


def test_questions_may_depend_on_the_data(probe, monkeypatch):
    monkeypatch.setitem(PLUGIN_REGISTRY, "DIALOG_PROBE", SimpleNamespace(
        name="DIALOG_PROBE", form=lambda dataset, options: None if "GROUP" in options else form("t", [
            field("GROUP", "묶을 열", "choice", choices=[column.name for column in dataset.columns_with_tag("SEX")], required=True)])))
    spec, dataset = PLUGIN_REGISTRY["DIALOG_PROBE"], read_tame(probe.path)
    assert dialogs.plugin_form(spec, dataset, {"GROUP": "성별"}) is None
    assert [value for value, _ in dialogs.plugin_form(spec, dataset, {}).fields[0].choices] == ["성별"]
    answering(monkeypatch, {"GROUP": "성별"})
    assert dialogs.plugin_options(spec, dataset, {"X": 1}, requested=True) == ({"X": 1, "GROUP": "성별"}, ["GROUP=성별"])
    assert dialogs.plugin_options(spec, dataset, {"GROUP": "성별"}, requested=True) == ({"GROUP": "성별"}, None)
    monkeypatch.setitem(PLUGIN_REGISTRY, "DIALOG_PROBE", SimpleNamespace(name="DIALOG_PROBE", form="GROUP"))
    with pytest.raises(TypeError, match="form must be a tametools.dialogs.Form"):
        dialogs.plugin_form(PLUGIN_REGISTRY["DIALOG_PROBE"], dataset, {})


def test_the_tutorial_plugin_asks_for_its_column_and_counts(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(ROOT))                          # the example module lives in the repository (tutorial.plugin_examples)
    sample = ROOT / "tutorial" / "05_plugins" / "sample_dialog_plugin.tame"
    monkeypatch.setattr(dialogs, "someone_is_there", lambda: True)
    seen = []
    answering(monkeypatch, {"GROUP": "검사항목명", "ORDER": "이름 순"}, seen)
    try:
        assert _main(["run-plugin", str(sample), "COUNT_BY_COLUMN", "--allow-plugins"]) == 0
        printed = capsys.readouterr().out
        assert seen[0][0].title == "열별 건수" and "answered: GROUP=검사항목명, MIN_COUNT=0, ORDER=NAME" in printed
        assert "count_by_column group=검사항목명 rows=2 min_count=0 order=NAME" in printed
        assert _main(["run-plugin", str(sample), "COUNT_BY_COLUMN", "--allow-plugins", "--option", "GROUP=성별", "--option", "MIN_COUNT=5"]) == 0
        assert len(seen) == 1 and "count_by_column group=성별 rows=0 min_count=5 order=COUNT" in capsys.readouterr().out
    finally:
        PLUGIN_REGISTRY.pop("COUNT_BY_COLUMN", None)
        sys.modules.pop("tutorial.plugin_examples.dialog_form_plugin", None)


# --- QC action and review records ----------------------------------------------------------------------------------------

DAY_20, DAY_21 = "2026-10-31T09:00:00+09:00", "2026-11-01T09:00:00+09:00"
RUN = "2026-11-01T08:30:00"
ACTION = {"cause": "시약 lot 교체 후 상승", "action": "재보정 후 관리물질 재측정", "by": "홍길동"}


@pytest.fixture
def lab(tmp_path):
    """Twenty days synced, then the next day's level-1 row: glucose (2_2s) and creatinine (10x) are rejected."""
    project = tmp_path / "qc_chem.tame"
    shutil.copy(TUTORIAL / "qc_chem.tame", project)
    excel = demo.write_workbook(tmp_path / "qc_chem_daily.xlsx")
    sync_project(project, now=DAY_20)
    demo.append_measurement(excel, demo.NEXT_DAY_LEVEL_1)
    sync_project(project, now=DAY_21)
    return SimpleNamespace(project=project, report=tmp_path / "qc_chem_report.html")


def records(lab):
    return sync_records(read_tame(lab.project))


def test_qc_action_dialog_offers_the_waiting_rejections_and_records_the_chosen_one(lab, monkeypatch, capsys):
    seen = []
    answering(monkeypatch, {**ACTION, "target": "2026-11-01 08:30 · Glucose · 수준 1 · 2_2s"}, seen)
    assert _main(["qc-action", str(lab.project), "--dialog"]) == 0
    made, start = seen[0]
    assert (made.title, made.intro, made.submit) == ("QC 조치 기록", "조치 기록이 없는 거부 run 2건", "저장")
    assert [text for _, text in made.fields[0].choices] == [
        "2026-11-01 08:30 · Creatinine, Glucose (한 기록으로 함께)", "2026-11-01 08:30 · Creatinine · 수준 1 · 10x",
        "2026-11-01 08:30 · Glucose · 수준 1 · 2_2s"]
    assert start["target"] == "" and start["by"] == ""              # several to choose from: nothing is preselected
    assert [(item.key, item.required) for item in made.fields] == [
        ("target", True), ("cause", True), ("action", True), ("outcome", False), ("patient_impact", False), ("by", True)]
    record = records(lab)[0]
    assert (record["RUN"], record["RESULTS"], record["CAUSE"], record["ACTION"], record["BY"]) == (
        RUN, ["glucose"], ACTION["cause"], ACTION["action"], "홍길동")
    assert "조치 기록 #1 을(를) 남겼습니다" in capsys.readouterr().out
    assert "시약 lot 교체 후 상승" in lab.report.read_text(encoding="utf-8")
    assert [entry["name"] for entry in action_status(lab.project)["open"]] == ["Creatinine"]


def test_qc_action_dialog_can_cover_the_analytes_rejected_together_then_has_nothing_left(lab, monkeypatch, capsys):
    answering(monkeypatch, {**ACTION, "target": "2026-11-01 08:30 · Creatinine, Glucose (한 기록으로 함께)"})
    assert _main(["qc-action", str(lab.project), "--dialog", "--outcome", "재측정 관리 내"]) == 0
    record = records(lab)[0]
    assert (record["RESULTS"], record["OUTCOME"]) == (["creatinine", "glucose"], "재측정 관리 내")
    assert action_status(lab.project)["open"] == []
    capsys.readouterr()
    assert _main(["qc-action", str(lab.project), "--dialog"]) == 0   # nothing waits: said so, nothing asked or written
    assert "조치 기록이 없는 거부 run 이 없습니다" in capsys.readouterr().out and len(records(lab)) == 1


def test_qc_action_dialog_starts_from_the_command_line_and_cancel_writes_nothing(lab, monkeypatch, capsys):
    seen = []
    answering(monkeypatch, None, seen)
    before = lab.project.read_bytes()
    assert _main(["qc-action", str(lab.project), "--dialog", "--run", "2026-11-01", "--result", "Glucose", "--cause", "원인 미상"]) == 1
    made, start = seen[0]
    assert made.intro == "run 2026-11-01 · Glucose" and made.fields[0].key == "cause" and start["cause"] == "원인 미상"
    assert "취소했습니다. 기록을 남기지 않았습니다." in capsys.readouterr().out and lab.project.read_bytes() == before
    answering(monkeypatch, {"action": "추세 관찰", "by": "홍길동"})
    assert _main(["qc-action", str(lab.project), "--dialog", "--run", "2026-11-01", "--result", "Glucose", "--cause", "원인 미상"]) == 0
    assert (records(lab)[0]["CAUSE"], records(lab)[0]["RESULTS"]) == ("원인 미상", ["glucose"])


def test_qc_action_correction_dialog_starts_from_the_earlier_record(lab, monkeypatch):
    add_action(lab.project, run="2026-11-01", results=["Glucose"], outcome="재측정 관리 내", **ACTION)
    seen = []
    answering(monkeypatch, {"cause": "보정 이탈", "by": "김책임"}, seen)
    assert _main(["qc-action", str(lab.project), "--dialog", "--supersedes", "1"]) == 0
    made, start = seen[0]
    assert made.intro.startswith(f"조치 기록 #1 (run {RUN} · glucose) 을(를) 정정합니다.") and made.fields[0].key == "cause"
    assert start == {"cause": ACTION["cause"], "action": ACTION["action"], "outcome": "재측정 관리 내", "patient_impact": "", "by": ""}
    second = records(lab)[1]
    assert (second["SUPERSEDES"], second["CAUSE"], second["ACTION"], second["RESULTS"], second["BY"]) == (
        1, "보정 이탈", ACTION["action"], ["glucose"], "김책임")


def test_qc_action_checks_that_a_record_can_be_saved_before_anyone_types(lab, monkeypatch):
    seen = []
    answering(monkeypatch, ACTION, seen)
    with pytest.raises(ValueError, match="정정할 기록 #7 이\\(가\\) 없거나"):
        _main(["qc-action", str(lab.project), "--dialog", "--supersedes", "7"])
    text = lab.project.read_text(encoding="utf-8")
    lab.project.write_text(text.replace('TITLE = "', 'TITLE = "수정 '), encoding="utf-8")        # settings edited after the sync
    assert 'TITLE = "수정 ' in lab.project.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="마지막 동기화 이후 직접 수정되었습니다"):
        _main(["qc-action", str(lab.project), "--dialog"])
    assert seen == []


def test_qc_commands_ask_by_themselves_only_when_a_person_left_something_out(lab, monkeypatch, capsys):
    seen = []
    answering(monkeypatch, ACTION, seen)
    with pytest.raises(ValueError, match="--run 으로 지정하세요"):       # unattended: the same error as before
        _main(["qc-action", str(lab.project)])
    monkeypatch.setattr(dialogs, "someone_is_there", lambda: True)
    with pytest.raises(ValueError, match="--run 으로 지정하세요"):       # --json is a program asking
        _main(["qc-action", str(lab.project), "--json"])
    with pytest.raises(ValueError):
        _main(["qc-action", str(lab.project), "--no-dialog"])
    assert seen == []
    assert _main(["qc-action", str(lab.project), "--run", "2026-11-01", "--result", "Glucose", "--cause", "c", "--action", "a", "--by", "b"]) == 0
    assert seen == [] and len(records(lab)) == 1                     # complete: recorded without a question
    assert _main(["qc-action", str(lab.project)]) == 0
    assert len(seen) == 1 and seen[0][1]["target"] == "2026-11-01 08:30 · Creatinine · 수준 1 · 10x"   # the only one left is preselected
    assert records(lab)[1]["RESULTS"] == ["creatinine"]


def test_qc_review_dialog_proposes_the_period_not_yet_reviewed(lab, monkeypatch, capsys):
    seen = []
    answering(monkeypatch, {"end": "2026-10-25", "by": "김책임"}, seen)
    assert _main(["qc-review", str(lab.project), "--dialog"]) == 0
    made, start = seen[0]
    assert (made.title, made.intro) == ("QC 검토 기록", "결과 기간 2026-10-12 ~ 2026-11-01 · 조치 기록이 없는 거부 run 2건")
    assert [(item.key, item.kind, item.required) for item in made.fields] == [
        ("start", "date", True), ("end", "date", True), ("by", "text", True), ("note", "multiline", False)]
    assert start == {"start": "2026-10-12", "end": "2026-11-01", "by": "", "note": ""}
    first = records(lab)[0]
    assert (first["KIND"], first["FROM"], first["TO"], first["BY"]) == ("REVIEW", "2026-10-12", "2026-10-25", "김책임")
    answering(monkeypatch, {"by": "김책임", "note": "미조치 2건 확인"}, seen)
    assert _main(["qc-review", str(lab.project), "--dialog"]) == 0
    assert seen[1][1]["start"] == "2026-10-26"                       # carries on after the last review
    assert (records(lab)[1]["FROM"], records(lab)[1]["TO"], records(lab)[1]["NOTE"]) == ("2026-10-26", "2026-11-01", "미조치 2건 확인")
    answering(monkeypatch, None)
    assert _main(["qc-review", str(lab.project), "--dialog"]) == 1 and len(records(lab)) == 2
