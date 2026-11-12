"""A plugin's declared questions (register_plugin form=...) in the web workbench: the server sends them as fields,
the browser draws the form, and the answers come back as options that are checked, run and logged like any other."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tametools import dialogs
from tametools.cli import _main
from tametools.dialogs import field, form
from tametools.io import read_tame
from tametools.models import OperationOutput
from tametools.plugin_base import list_plugins, manager
from tametools.plugin_base.base import PLUGIN_REGISTRY, PluginSpec
from tametools.provenance import log_entries
from app.main import app
from app.tametools_bridge import (create_web_plugin_payload, dataset_from_payload, dataset_payload, plugin_form_payload,
    run_web_plugin_payload, select_plugins_payload)

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "tutorial" / "05_plugins" / "sample_dialog_plugin.tame"
TAME = ("<META>\n[INFO]\nDESCRIPTION = \"web form test\"\n</META>\n<DATA>\n"
        "[[TESTNAME]]검사항목명\t[[SEX]]성별\t[[AGE]]나이\t[[RESULT::<NUM>]]보고값\n"
        "AST\tM\t5\t10\nAST\tF\t15\t12\nALT\tM\t35\t30\nALT\tF\t82\t40\n</DATA>\n")
QUESTIONS = form("분석 설정", [
    field("GROUP", "묶을 열", "column", required=True, hint="이 열로 묶습니다"),
    field("MIN_N", "최소 자료 수", "integer", default=2, minimum=1, maximum=99),
    field("MODE", "방식", "choice", choices=[("MEAN", "평균"), ("MEDIAN", "중앙값")], default="MEAN"),
    field("LEVELS", "수준", "multi", choices=[(1, "저"), (2, "중"), (3, "고")]),
    field("ROBUST", "이상치 제외", "check"),
    field("SINCE", "시작일", "date"),
    field("NOTE", "비고", "multiline"),
    field("TEMPLATE", "양식 파일", "file"),
], intro="어떻게 계산할지 정합니다.", submit="실행")


@pytest.fixture(autouse=True)
def no_window(monkeypatch):
    """The server never asks by itself: a window or a terminal prompt opened by a request would be a failure."""
    def refuse(*args, **kwargs):
        raise AssertionError("the web server must not open a dialog")
    for name in ("ask", "_ask_gui", "_ask_terminal"):
        monkeypatch.setattr(dialogs, name, refuse)


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """A bundled (trusted) plugin that declares its questions, one that declares none, and a small file to run them on."""
    received = []

    def handler(dataset, meta, step_name, options):
        received.append(dict(options))
        return OperationOutput(name=step_name, dataset=dataset, message="probe ran")

    list_plugins()                                              # the bundled plugins are loaded before the probes are added
    for spec in (PluginSpec("WEB_FORM_PROBE", "declares questions", handler, form=QUESTIONS),
                 PluginSpec("WEB_PLAIN_PROBE", "declares none", handler)):
        monkeypatch.setitem(PLUGIN_REGISTRY, spec.name, spec)
        monkeypatch.setitem(manager._TRUSTED_PLUGINS, spec.name, spec)
    path = tmp_path / "data.tame"
    path.write_text(TAME, encoding="utf-8")
    return SimpleNamespace(received=received, workspace=tmp_path, path=path,
                           payload=dataset_payload(read_tame(path), filename="data.tame"))


def with_meta(payload, text):
    return {**payload, "metaText": payload["metaText"] + "\n" + text}


def post(client, route, payload, **options):
    return client.post(route, json={"dataset": payload, "options": options})


def test_the_plugin_list_says_which_plugins_declare_questions(probe):
    """So the browser sends the data for a form only when there is one, and shows the trust step without a request."""
    listed = {item["name"]: item["questions"] for item in dataset_payload(read_tame(probe.path))["plugins"]}
    assert (listed["WEB_FORM_PROBE"], listed["WEB_PLAIN_PROBE"], listed["QC_ANALYSIS"]) == ("declared", "none", "none")
    written = create_web_plugin_payload(probe.payload, {"name": "MY_ROWS", "source": "def run(dataset, options):\n    return dataset"}, probe.workspace)
    named = select_plugins_payload(written, {"enabled": ["MY_ROWS", "COUNT_BY_COLUMN"]})
    # written in the workbench: cannot declare any. Named but not found among the bundled ones: in external code, unread
    assert {item["name"]: item["questions"] for item in named["plugins"]} == {"MY_ROWS": "none", "COUNT_BY_COLUMN": "unknown"}


def test_a_plugin_without_questions_keeps_the_json_box(probe):
    with TestClient(app) as client:
        for name in ("WEB_PLAIN_PROBE", "QC_ANALYSIS", "RI_EP28"):
            response = post(client, "/api/plugins/form", probe.payload, plugin=name)
            assert response.status_code == 200, response.text
            assert response.json()["form"] is None and response.json()["requiresTrust"] is False
        assert post(client, "/api/plugins/form", probe.payload).status_code == 400              # which plugin?
        ran = post(client, "/api/plugins/run", probe.payload, plugin="WEB_PLAIN_PROBE", pluginOptions={"rows": 3})
        assert ran.status_code == 200 and probe.received == [{"rows": 3}]
        refused = post(client, "/api/plugins/run", probe.payload, plugin="WEB_PLAIN_PROBE", answers={"GROUP": "성별"})
        assert refused.status_code == 400 and "질문을 선언하지 않았습니다" in refused.json()["detail"]
    assert len(probe.received) == 1


def test_declared_questions_come_as_fields_for_the_browser_to_draw(probe):
    with TestClient(app) as client:
        response = post(client, "/api/plugins/form", probe.payload, plugin="WEB_FORM_PROBE")
    assert response.status_code == 200, response.text
    made = response.json()["form"]
    assert (made["title"], made["intro"], made["submit"]) == ("분석 설정", "어떻게 계산할지 정합니다.", "실행")
    fields = {item["key"]: item for item in made["fields"]}
    assert list(fields) == ["GROUP", "MIN_N", "MODE", "LEVELS", "ROBUST", "SINCE", "NOTE", "TEMPLATE"]
    assert all(set(item) == {"key", "label", "kind", "required", "choices", "hint", "minimum", "maximum", "value"} for item in fields.values())
    # a column question arrives as a choice among the columns of this dataset
    assert fields["GROUP"] == {"key": "GROUP", "label": "묶을 열", "kind": "choice", "required": True, "hint": "이 열로 묶습니다",
                               "minimum": None, "maximum": None, "value": "",
                               "choices": [{"value": name, "label": name} for name in ("검사항목명", "성별", "나이", "보고값")]}
    assert (fields["MIN_N"]["kind"], fields["MIN_N"]["minimum"], fields["MIN_N"]["maximum"], fields["MIN_N"]["value"]) == ("integer", 1, 99, 2)
    assert fields["MODE"]["choices"] == [{"value": "MEAN", "label": "평균"}, {"value": "MEDIAN", "label": "중앙값"}] and fields["MODE"]["value"] == "MEAN"
    assert [fields[key]["kind"] for key in ("LEVELS", "ROBUST", "SINCE", "NOTE", "TEMPLATE")] == ["multi", "check", "date", "multiline", "file"]
    assert [fields[key]["value"] for key in ("LEVELS", "ROBUST", "SINCE", "NOTE", "TEMPLATE")] == [[], False, "", "", ""]
    assert not any(item["required"] for key, item in fields.items() if key != "GROUP")


def test_the_form_starts_from_the_settings_in_the_file_and_the_request(probe):
    payload = with_meta(probe.payload, '[WEB_FORM_PROBE]\nGROUP = "성별"\nmin_n = "5"\nMODE = "TRIMMED"\nLEVELS = "1,3"\nROBUST = "yes"\n')
    made = plugin_form_payload(payload, {"plugin": "WEB_FORM_PROBE", "pluginOptions": {"SINCE": "2026-11-01"}}, probe.workspace)["form"]
    fields = {item["key"]: item for item in made["fields"]}
    # typed as an answer would be: the text "5" is the number 5, "1,3" the choices 1 and 3
    assert {key: item["value"] for key, item in fields.items()} == {
        "GROUP": "성별", "MIN_N": 5, "MODE": "TRIMMED", "LEVELS": [1, 3], "ROBUST": True, "SINCE": "2026-11-01", "NOTE": "", "TEMPLATE": ""}
    # a current value outside the declared choices stays selectable, so opening the form and running does not drop it
    assert fields["MODE"]["choices"][-1] == {"value": "TRIMMED", "label": "TRIMMED"}
    run_web_plugin_payload(payload, {"plugin": "WEB_FORM_PROBE", "answers": {key: item["value"] for key, item in fields.items()}}, probe.workspace)
    assert probe.received == [{"GROUP": "성별", "MIN_N": 5, "MODE": "TRIMMED", "LEVELS": [1, 3], "ROBUST": True, "SINCE": "2026-11-01"}]


def test_a_required_question_left_empty_is_refused_by_name_and_nothing_runs(probe):
    with TestClient(app) as client:
        for answers in ({"GROUP": "", "MIN_N": 3}, {"GROUP": None}, {"MIN_N": 3}):
            response = post(client, "/api/plugins/run", probe.payload, plugin="WEB_FORM_PROBE", answers=answers)
            assert response.status_code == 400, response.text
            body = response.json()
            assert body["detail"] == "'묶을 열' 은(는) 필수 항목입니다" and body["fieldErrors"] == {"GROUP": "'묶을 열' 은(는) 필수 항목입니다"}
            assert "auditFile" not in body                       # nothing ran, so there is no failed run to keep
        # without a form at all (an API client, as an unattended command line): the missing setting is named the same way
        bare = post(client, "/api/plugins/run", probe.payload, plugin="WEB_FORM_PROBE", pluginOptions={"MIN_N": 3})
        assert bare.status_code == 400 and bare.json()["fieldErrors"] == {"GROUP": "'묶을 열' 은(는) 필수 항목입니다"}
        # every answer that cannot be accepted is named, not only the first
        wrong = post(client, "/api/plugins/run", probe.payload, plugin="WEB_FORM_PROBE",
                     answers={"GROUP": "없는 열", "MIN_N": 0, "MODE": "MEDIAN", "SINCE": "11/01"})
        assert wrong.status_code == 400 and set(wrong.json()["fieldErrors"]) == {"GROUP", "MIN_N", "SINCE"}
        assert "1 이상" in wrong.json()["fieldErrors"]["MIN_N"] and "'최소 자료 수'" in wrong.json()["detail"]
        stray = post(client, "/api/plugins/run", probe.payload, plugin="WEB_FORM_PROBE", answers={"GROUP": "성별", "GRUOP": "나이"})
        assert stray.status_code == 400 and "묻지 않은 항목" in stray.json()["detail"] and "GRUOP" in stray.json()["detail"]
    assert probe.received == []


def test_answers_become_options_run_the_plugin_and_stay_in_the_log(probe):
    answers = {"GROUP": "검사항목명", "MIN_N": "3", "MODE": "중앙값", "LEVELS": [3, 1], "ROBUST": True, "SINCE": "2026-11-01",
               "NOTE": " 재검 포함 ", "TEMPLATE": ""}
    with TestClient(app) as client:
        response = post(client, "/api/plugins/run", probe.payload, plugin="WEB_FORM_PROBE", pluginOptions={"EXTRA": 1}, answers=answers)
    assert response.status_code == 200, response.text
    # typed and checked as the dialog window would hand them over; what the form does not ask passes through
    expected = {"EXTRA": 1, "GROUP": "검사항목명", "MIN_N": 3, "MODE": "MEDIAN", "LEVELS": [3, 1], "ROBUST": True,
                "SINCE": "2026-11-01", "NOTE": "재검 포함"}
    assert probe.received == [expected]
    entry = log_entries(dataset_from_payload(response.json(), standardize=False))[-1]
    assert entry["OPERATION"] == "PLUGIN-RUN" and entry["PARAMS"]["options"] == expected
    assert entry["EFFECTIVE_PARAMS"]["PLUGIN_REQUESTED_OPTIONS"] == expected
    # the logged options repeat the run without a form
    repeated = run_web_plugin_payload(probe.payload, {"plugin": "WEB_FORM_PROBE", "pluginOptions": expected}, probe.workspace)
    assert probe.received[1] == expected and repeated["rowCount"] == 4


def test_an_answer_wins_over_the_file_and_an_emptied_answer_removes_the_setting(probe):
    payload = with_meta(probe.payload, '[WEB_FORM_PROBE]\nGROUP = "성별"\nMIN_N = 5\nNOTE = "파일에 적힌 비고"\nKEEP = "x"\n')
    run_web_plugin_payload(payload, {"plugin": "WEB_FORM_PROBE", "pluginOptions": {"group": "나이"},
                                     "answers": {"GROUP": "보고값", "MIN_N": None, "NOTE": ""}}, probe.workspace)
    # MIN_N and NOTE were emptied, so the plugin's own defaults apply; what was not sent keeps its starting value
    # (MODE its default, the check box unticked: a check box is always an answer, as in the dialog window)
    assert probe.received == [{"KEEP": "x", "GROUP": "보고값", "MODE": "MEAN", "ROBUST": False}]
    # without answers the settings pass as they are, as on the command line when nothing is asked
    run_web_plugin_payload(payload, {"plugin": "WEB_FORM_PROBE"}, probe.workspace)
    assert probe.received[1] == {"GROUP": "성별", "MIN_N": 5, "NOTE": "파일에 적힌 비고", "KEEP": "x"}


def test_questions_may_depend_on_the_data_and_a_plugin_may_decide_not_to_ask(probe, monkeypatch):
    def depends(dataset, options):
        return None if "GROUP" in options else form("t", [field("GROUP", "묶을 열", "column", tags=["SEX"], required=True)])
    spec = PluginSpec("WEB_FORM_PROBE", "", PLUGIN_REGISTRY["WEB_FORM_PROBE"].handler, form=depends)
    monkeypatch.setitem(PLUGIN_REGISTRY, spec.name, spec)
    monkeypatch.setitem(manager._TRUSTED_PLUGINS, spec.name, spec)
    asked = plugin_form_payload(probe.payload, {"plugin": "WEB_FORM_PROBE"}, probe.workspace)["form"]
    assert [choice["value"] for choice in asked["fields"][0]["choices"]] == ["성별"]
    assert plugin_form_payload(probe.payload, {"plugin": "WEB_FORM_PROBE", "pluginOptions": {"GROUP": "성별"}}, probe.workspace)["form"] is None


def test_an_external_plugin_shows_its_questions_only_after_trust_and_runs_as_on_the_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(ROOT))                      # the example module lives in the repository (tutorial.plugin_examples)
    module = "tutorial.plugin_examples.dialog_form_plugin"
    monkeypatch.delitem(sys.modules, module, raising=False)
    payload = dataset_payload(read_tame(SAMPLE), filename=SAMPLE.name)
    try:
        with TestClient(app) as client:
            closed = post(client, "/api/plugins/form", payload, plugin="COUNT_BY_COLUMN")
            assert closed.status_code == 200 and closed.json() == {"plugin": "COUNT_BY_COLUMN", "requiresTrust": True, "form": None}
            assert module not in sys.modules                    # looking at the questions means importing the code: not before trust
            opened = post(client, "/api/plugins/form", payload, plugin="COUNT_BY_COLUMN", allowPlugins=True).json()
            assert opened["form"]["title"] == "열별 건수" and opened["requiresTrust"] is False
            assert [(item["label"], item["kind"], item["required"], item["value"]) for item in opened["form"]["fields"]] == [
                ("묶을 열", "choice", True, ""), ("최소 건수", "integer", False, 0), ("정렬", "choice", False, "COUNT")]
            assert [choice["label"] for choice in opened["form"]["fields"][0]["choices"]] == ["검사항목명", "성별", "나이", "보고값"]
            empty = post(client, "/api/plugins/run", payload, plugin="COUNT_BY_COLUMN", allowPlugins=True,
                         answers={item["key"]: item["value"] for item in opened["form"]["fields"]})
            assert empty.status_code == 400 and list(empty.json()["fieldErrors"]) == ["GROUP"]
            ran = post(client, "/api/plugins/run", payload, plugin="COUNT_BY_COLUMN", allowPlugins=True,
                       answers={"GROUP": "검사항목명", "MIN_COUNT": 0, "ORDER": "NAME"})
        assert ran.status_code == 200, ran.text
        result = dataset_from_payload(ran.json(), standardize=False)
        logged = log_entries(result)[-1]
        assert logged["PARAMS"]["options"] == {"GROUP": "검사항목명", "MIN_COUNT": 0, "ORDER": "NAME"}
        assert logged["SUMMARY"] == "count_by_column group=검사항목명 rows=2 min_count=0 order=NAME"
        # the same answers given as --option on the command line count the same
        saved = tmp_path / "cli.tame"
        assert _main(["run-plugin", str(SAMPLE), "COUNT_BY_COLUMN", "--allow-plugins", "--no-dialog", "--option", "GROUP=검사항목명",
                      "--option", "MIN_COUNT=0", "--option", "ORDER=NAME", "--output", str(saved)]) == 0
        assert "count_by_column group=검사항목명 rows=2 min_count=0 order=NAME" in capsys.readouterr().out
        command_line = read_tame(saved)
        assert [column.name for column in result.columns] == [column.name for column in command_line.columns] == ["값", "건수"]
        assert result.df.astype(str).values.tolist() == command_line.df.astype(str).values.tolist() == [["ALT", "4"], ["AST", "4"]]
    finally:
        PLUGIN_REGISTRY.pop("COUNT_BY_COLUMN", None)
        sys.modules.pop(module, None)


def test_a_plugin_written_in_the_workbench_is_not_imported_to_look_for_questions(probe, tmp_path):
    marker = tmp_path / "executed.txt"
    written = create_web_plugin_payload(probe.payload, {"name": "MY_ROWS",
        "source": f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef run(dataset, options):\n    return dataset"}, tmp_path)
    # it cannot declare questions, so there is nothing to trust it for here: the JSON box, and no code run
    assert plugin_form_payload(written, {"plugin": "MY_ROWS"}, tmp_path) == {"plugin": "MY_ROWS", "requiresTrust": False, "form": None}
    assert not marker.exists()
    assert plugin_form_payload(written, {"plugin": "MY_ROWS", "allowPlugins": True}, tmp_path)["form"] is None
