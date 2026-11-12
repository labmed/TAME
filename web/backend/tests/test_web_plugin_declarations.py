"""The workbench knows its own screens, not the plugins behind them: which plugin is run through the reference-interval
screen, and which plugins are older ones kept for existing files, comes from what plugins declare
(register_plugin(panel=..., superseded_by=...)). What is written to files stays what it was."""
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tametools.io import read_tame
from tametools.plugin_base import list_plugins, manager
from tametools.plugin_base.base import PLUGIN_REGISTRY
from tametools.provenance import log_entries
from app.main import app, store
from app.tametools_bridge import (LEGACY_ANALYSIS_PLUGIN, dataset_from_payload, dataset_payload, plugin_form_payload,
    run_web_plugin_payload, select_plugins_payload)
from app.workbench_analysis import (REFERENCE_PANEL, reference_analysis, reference_plugin, reference_plugin_names,
    reference_result_key)

ROOT = Path(__file__).resolve().parents[3]
OPTIONS = {"RESULT_IDS": ["alt"], "OUTLIER_METHOD": "NONE"}


@pytest.fixture
def kenya():
    """Reference individuals with declared units; the file carries its settings for the reference-interval screen."""
    return dataset_payload(read_tame(ROOT / "web/backend/examples/kenya_reference.tame"), filename="kenya.tame")


def declare(monkeypatch, **panels):
    """Change which bundled plugin declares a host screen, as another build of the plugins might."""
    list_plugins()
    for name, panel in panels.items():
        spec = replace(manager._TRUSTED_PLUGINS[name], panel=panel)
        monkeypatch.setitem(manager._TRUSTED_PLUGINS, name, spec)
        monkeypatch.setitem(PLUGIN_REGISTRY, name, spec)


def test_the_plugin_list_carries_what_each_plugin_declares_about_the_host(kenya):
    assert REFERENCE_PANEL == "reference_interval"
    listed = {item["name"]: item for item in kenya["pluginCatalog"]}
    assert all({"panel", "supersededBy"} <= set(item) for item in listed.values())
    assert {name: item["panel"] for name, item in listed.items() if item["panel"]} == {"RI_EP28": "reference_interval"}
    assert {name: item["supersededBy"] for name, item in listed.items() if item["supersededBy"]} == {
        "REFERENCE_INTERVAL": "RI_EP28", "RI": "RI_EP28"}
    # a name the file asks for that is not installed declares nothing
    named = select_plugins_payload(kenya, {"enabled": ["MISSING_PACKAGE"]})["plugins"][0]
    assert (named["available"], named["panel"], named["supersededBy"]) == (False, "", "")
    # the workbench reads the same declaration: one plugin, the names it goes by, and the plugins it replaces
    assert reference_plugin() == "RI_EP28" and reference_result_key() == "RI_EP28_RESULT"
    assert reference_plugin_names() == ["REFERENCE_INTERVAL_EP28", "RI_EP28"]
    assert reference_plugin_names(superseded=True) == ["REFERENCE_INTERVAL_EP28", "RI_EP28", "REFERENCE_INTERVAL", "RI"]


def test_a_saved_analysis_says_which_screen_it_is_set_up_in(kenya):
    plugins = {"A": "RI_EP28", "B": "REFERENCE_INTERVAL_EP28", "C": "REFERENCE_INTERVAL", "D": "RI", "E": "QC_ANALYSIS", "F": "ri_ep28"}
    text = "".join(f'\n[ANALYSES.{name}]\nPLUGIN = "{plugin}"\n' for name, plugin in plugins.items()) + "\n[ANALYSES.G]\nLABEL = \"old file\"\n"
    analyses = {item["name"]: item for item in dataset_payload(dataset_from_payload({**kenya, "metaText": kenya["metaText"] + text},
                                                                                     standardize=False))["analyses"]}
    # the plugin of the screen under each of its names, and the older plugins it replaces; a name is compared as written
    assert {name: item["panel"] for name, item in analyses.items()} == {
        "A": "reference_interval", "B": "reference_interval", "C": "reference_interval", "D": "reference_interval", "E": "", "F": "", "G": ""}
    # an entry that names no plugin is from before the key existed and still means the same plugin
    assert analyses["G"]["plugin"] == LEGACY_ANALYSIS_PLUGIN == "CHEMISTRY_ANALYSIS"


def test_the_reference_screen_runs_the_declared_plugin_and_writes_what_it_always_wrote(kenya):
    assert set(kenya["referenceSettings"]) >= {"METHOD", "OUTLIER_METHOD", "POPULATION"}       # the file's [RI_EP28] section
    with TestClient(app) as client:
        response = client.post("/api/datasets/reference-interval", json={"dataset": kenya, "options": OPTIONS})
    assert response.status_code == 200, response.text[:2000]
    body = response.json()
    assert body["filename"] == "kenya.ri_ep28.tame" and body["resultType"] == "reference_interval" and body["dataRole"] == "result"
    assert body["analysisSettings"]["OUTLIER_METHOD"] == "NONE" and body["analysisTables"]["reference_intervals"]
    result = dataset_from_payload(body, standardize=False)
    assert "RI_EP28_RESULT" in result.meta and result.meta["WEB_WORKBENCH"]["ANALYSIS"] == "RI_EP28"
    entries = log_entries(result)
    assert [entry["OPERATION"] for entry in entries[-2:]] == ["REFERENCE_INTERVAL_EP28", "REFERENCE-INTERVAL"]
    assert entries[-1]["PARAMS"]["plugin"] == "RI_EP28" and entries[-1]["PARAMS"]["options"]["RESULT_IDS"] == ["alt"]
    assert entries[-1]["EFFECTIVE_PARAMS"]["PLUGIN_IMPLEMENTATION"]["NAME"] == "RI_EP28"
    # the input kept for re-verification carries the applied options in the same section, and the log names it
    kept = read_tame(store.path / "analysis_reports" / result.meta["WEB_WORKBENCH"]["REPORT_ID"] / "analysis_input.tame")
    assert kept.meta["RI_EP28"]["RESULT_IDS"] == ["alt"] and "OUTPUT_DIR" not in kept.meta["RI_EP28"]
    assert log_entries(kept)[-1]["OPERATION"] == "ANALYSIS_INPUT" and list(log_entries(kept)[-1]["PARAMS"]) == ["RI_EP28"]


def test_running_that_plugin_from_the_plugin_tab_goes_through_the_same_screen(kenya, tmp_path):
    for name in ("RI_EP28", "REFERENCE_INTERVAL_EP28"):
        assert plugin_form_payload(kenya, {"plugin": name}, tmp_path) == {"plugin": name, "requiresTrust": False, "form": None}
        result = run_web_plugin_payload(kenya, {"plugin": name, "pluginOptions": OPTIONS}, None)
        assert (result["resultType"], result["filename"]) == ("reference_interval", "kenya.ri_ep28.tame")
        assert log_entries(dataset_from_payload(result, standardize=False))[-1]["PARAMS"]["plugin"] == "RI_EP28"


def test_the_names_written_are_those_of_the_plugin_that_declares_the_screen(kenya, monkeypatch):
    # Were the screen declared under the plugin's other name, that name would be the section, the file name and the log.
    declare(monkeypatch, RI_EP28="", REFERENCE_INTERVAL_EP28=REFERENCE_PANEL)
    assert reference_plugin() == "REFERENCE_INTERVAL_EP28" and reference_result_key() == "REFERENCE_INTERVAL_EP28_RESULT"
    moved = {**kenya, "metaText": kenya["metaText"].replace("[RI_EP28", "[REFERENCE_INTERVAL_EP28")}
    assert dataset_payload(dataset_from_payload(kenya, standardize=False))["referenceSettings"] == {}
    listed = dataset_payload(dataset_from_payload(moved, standardize=False))
    assert listed["referenceSettings"]["METHOD"] == kenya["referenceSettings"]["METHOD"]
    assert [item["name"] for item in listed["pluginCatalog"] if item["panel"]] == ["REFERENCE_INTERVAL_EP28"]
    result = reference_analysis(moved, dict(OPTIONS))
    assert result["filename"] == "kenya.reference_interval_ep28.tame"
    produced = dataset_from_payload(result, standardize=False)
    assert produced.meta["WEB_WORKBENCH"]["ANALYSIS"] == "REFERENCE_INTERVAL_EP28"
    assert log_entries(produced)[-1]["PARAMS"]["plugin"] == "REFERENCE_INTERVAL_EP28"


def test_without_a_plugin_for_the_reference_screen_the_rest_of_the_workbench_works(kenya, monkeypatch, tmp_path):
    declare(monkeypatch, RI_EP28="")
    assert reference_plugin() == "" and reference_plugin_names(superseded=True) == [] and reference_result_key() is None
    opened = dataset_payload(dataset_from_payload(kenya, standardize=False), filename="kenya.tame")
    assert opened["referenceSettings"] == {} and opened["dataRole"] == "input" and opened["rowCount"] == kenya["rowCount"]
    assert not any(item["panel"] for item in opened["pluginCatalog"]) and len(opened["plugins"]) == len(kenya["plugins"])
    with TestClient(app) as client:
        refused = client.post("/api/datasets/reference-interval", json={"dataset": opened, "options": OPTIONS})
        assert refused.status_code == 400 and 'panel="reference_interval"' in refused.json()["detail"]
        # other work goes on: exploring the data, running another plugin, asking a plugin for its form
        assert client.post("/api/datasets/eda", json=opened).status_code == 200
        qc = dataset_payload(read_tame(ROOT / "web/backend/examples/qc_workflow.tame"), filename="qc.tame")
        ran = client.post("/api/plugins/run", json={"dataset": qc, "options": {"plugin": "QC_ANALYSIS"}})
        assert ran.status_code == 200 and ran.json()["rowCount"] == 48
        asked = client.post("/api/plugins/form", json={"dataset": opened, "options": {"plugin": "RI_EP28"}})
        assert asked.status_code == 200 and asked.json()["form"] is None
