"""The screen a plugin registered for its results (register_view) in the web workbench: a dataset response says whether
its result has one, and the screen itself is rendered only when asked for, under the trust rule of a plugin run."""
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from tametools.io import read_tame
from tametools.models import OperationOutput
from tametools.plugin_base import list_plugins, manager
from tametools.plugin_base.base import PLUGIN_REGISTRY, PluginSpec
from tametools.plugin_base.extensions import VIEW_REGISTRY, ViewSpec
from app.main import app
from app.tametools_bridge import (dataset_from_payload, dataset_payload, plugin_view_payload, run_meta_analysis_payload,
    run_web_plugin_payload)

ROOT = Path(__file__).resolve().parents[3]
DOCUMENT = "<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>probe</title></head><body><p>rows: {rows}</p></body></html>"


@pytest.fixture
def qc():
    """Control measurements with declared baselines: the QC example of the workbench, with every plugin available."""
    source = read_tame(ROOT / "web/backend/examples/qc_workflow.tame")
    source.meta.pop("PLUGINS", None)                # the example limits itself to the QC workflow plugins
    return dataset_payload(source, filename="qc.tame")


@pytest.fixture
def external(monkeypatch):
    """A plugin and its screen that are not bundled: registered, as after an import under trust, but not trusted."""
    rendered = []

    def handler(dataset, meta, step_name, options):
        result = dataset.replace(meta={"ANALYSIS_RESULT": {"PLUGIN": "EXT_VIEW_PROBE"}}, raw_sections={})
        return OperationOutput(name=step_name, dataset=result, message="external probe ran")

    def render(dataset):
        rendered.append(len(dataset.df))
        return DOCUMENT.format(rows=len(dataset.df))

    list_plugins()                                  # the bundled plugins and screens are fixed before the probe is added
    monkeypatch.setitem(PLUGIN_REGISTRY, "EXT_VIEW_PROBE", PluginSpec("EXT_VIEW_PROBE", "external probe", handler))
    monkeypatch.setitem(VIEW_REGISTRY, "EXT_VIEW_PROBE", ViewSpec("EXT_VIEW_PROBE", render, "외부 화면"))
    return rendered


def post(client, route, payload, **options):
    return client.post(route, json={"dataset": payload, "options": options})


def test_a_result_says_that_its_plugin_registered_a_screen_and_the_screen_is_one_complete_document(qc):
    assert qc["pluginView"] is None                 # measurements, not a result
    with TestClient(app) as client:
        ran = post(client, "/api/plugins/run", qc, plugin="WESTGARD_QC")
        assert ran.status_code == 200, ran.text[:2000]
        result = ran.json()
        # looked up, not rendered: the response names the screen and carries no HTML
        assert result["pluginView"] == {"name": "WESTGARD_QC", "title": "QC 보고서", "requiresTrust": False}
        assert "<svg" not in ran.text
        shown = post(client, "/api/plugins/view", result)
    assert shown.status_code == 200, shown.text[:2000]
    body = shown.json()
    assert (body["name"], body["title"]) == ("WESTGARD_QC", "QC 보고서") and set(body) == {"name", "title", "html"}
    html = body["html"]
    # one complete document ...
    assert html.lower().startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert [len(re.findall(tag, html, re.I)) for tag in (r"<html[\s>]", r"<head[\s>]", "</head>", r"<body[\s>]", "</body>")] == [1, 1, 1, 1, 1]
    assert re.search(r"<title>[^<]+</title>", html.split("</head>")[0]) and "charset='utf-8'" in html.split("</head>")[0]
    assert "Levey-Jennings" in html and html.count("<svg") >= 2
    # ... that refers to no outside file: nothing to load, nowhere to send
    assert not re.search(r"""(?:src|href|action)\s*=\s*["']?\s*(?:[a-z][a-z0-9+.-]*:)?//""", html, re.I)
    assert not re.search(r"<link\b|<script[^>]*\ssrc\s*=|<iframe\b|<object\b|<embed\b|@import|url\(\s*['\"]?(?:https?:)?//", html, re.I)
    assert not re.search(r"\b(?:fetch|XMLHttpRequest|WebSocket|sendBeacon|EventSource)\b", html)
    # the screen belongs to the result, so a result saved and opened again still has it
    assert dataset_payload(dataset_from_payload(result, standardize=False))["pluginView"] == result["pluginView"]


def test_a_result_without_a_screen_is_as_before(qc, tmp_path):
    with TestClient(app) as client:
        result = post(client, "/api/plugins/run", qc, plugin="QC_ANALYSIS").json()
        assert result["pluginView"] is None and "ANALYSIS_RESULT" in result["metaText"]
        refused = post(client, "/api/plugins/view", result)
        assert refused.status_code == 400 and "QC_ANALYSIS 은(는) 화면을 등록하지 않았습니다" in refused.json()["detail"]
        measurements = post(client, "/api/plugins/view", qc)
        assert measurements.status_code == 400 and "플러그인 결과가 아닙니다" in measurements.json()["detail"]


def test_a_result_may_name_the_screen_of_another_plugin(qc, tmp_path):
    result = run_web_plugin_payload(qc, {"plugin": "WESTGARD_QC"}, tmp_path)
    borrowed = {**result, "metaText": result["metaText"].replace('PLUGIN = "WESTGARD_QC"', 'PLUGIN = "SOMETHING_ELSE"\nVIEW = "WESTGARD_QC"', 1)}
    assert 'VIEW = "WESTGARD_QC"' in borrowed["metaText"]
    assert dataset_payload(dataset_from_payload(borrowed, standardize=False))["pluginView"]["name"] == "WESTGARD_QC"
    assert plugin_view_payload(borrowed, {}, tmp_path)["html"].lower().startswith("<!doctype html>")


def test_the_screen_of_an_external_plugin_waits_for_trust(qc, external, tmp_path):
    # run with the file's plugins trusted: the response offers the screen, marked as external code
    result = run_web_plugin_payload(qc, {"plugin": "EXT_VIEW_PROBE", "allowPlugins": True}, tmp_path)
    assert result["pluginView"] == {"name": "EXT_VIEW_PROBE", "title": "외부 화면", "requiresTrust": True}
    # the same result looked at without that trust (opened from a file, refreshed): no screen is offered
    assert dataset_payload(dataset_from_payload(result, standardize=False))["pluginView"] is None
    with TestClient(app) as client:
        closed = post(client, "/api/plugins/view", result)
        assert closed.status_code == 400 and "EXT_VIEW_PROBE 은(는) 외부 플러그인입니다" in closed.json()["detail"]
        assert external == []                       # its code did not run
        opened = post(client, "/api/plugins/view", result, allowPlugins=True)
    assert opened.status_code == 200, opened.text
    assert opened.json() == {"name": "EXT_VIEW_PROBE", "title": "외부 화면", "html": DOCUMENT.format(rows=24)}
    assert external == [24]
    # a saved analysis (META[ANALYSES]) run under trust offers the screen the same way
    planned = {**qc, "metaText": qc["metaText"] + '\n[ANALYSES.PROBE]\nPLUGIN = "EXT_VIEW_PROBE"\n'}
    assert run_meta_analysis_payload(planned, {"analysis": "PROBE", "allowPlugins": True}, tmp_path)["pluginView"]["requiresTrust"] is True


def test_a_screen_that_returns_no_document_is_an_error_not_an_empty_frame(qc, external, tmp_path, monkeypatch):
    result = run_web_plugin_payload(qc, {"plugin": "EXT_VIEW_PROBE", "allowPlugins": True}, tmp_path)
    for returned in ("", None, b"<html></html>"):
        monkeypatch.setitem(VIEW_REGISTRY, "EXT_VIEW_PROBE", ViewSpec("EXT_VIEW_PROBE", lambda dataset, returned=returned: returned, "외부 화면"))
        with TestClient(app) as client:
            response = post(client, "/api/plugins/view", result, allowPlugins=True)
        assert response.status_code == 400 and "must return an HTML document" in response.json()["detail"]


def test_a_trusted_screen_is_recognised_by_being_the_one_that_was_bundled(qc, external, tmp_path, monkeypatch):
    # An external file that registers a screen under a bundled plugin's name does not become that plugin's screen.
    bundled = manager._TRUSTED_VIEWS["WESTGARD_QC"]
    result = run_web_plugin_payload(qc, {"plugin": "WESTGARD_QC"}, tmp_path)
    monkeypatch.setitem(VIEW_REGISTRY, "WESTGARD_QC", ViewSpec("WESTGARD_QC", lambda dataset: DOCUMENT.format(rows=0), "가로챈 화면"))
    assert dataset_payload(dataset_from_payload(result, standardize=False))["pluginView"] is None
    with pytest.raises(ValueError, match="등록된 플러그인 화면이 없습니다"):
        plugin_view_payload(result, {}, tmp_path)
    monkeypatch.setitem(VIEW_REGISTRY, "WESTGARD_QC", bundled)
    assert plugin_view_payload(result, {}, tmp_path)["title"] == "QC 보고서"
