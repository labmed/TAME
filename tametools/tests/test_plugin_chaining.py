"""Plugins that use other plugins: what the inner one may do, what is recorded, what is passed on, what is checked.

Every scenario that depends on how plugin files are read runs the real command line in a process of its own.
"""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pandas as pd
import pytest

import tametools
from tametools import chain, meta_sections, pipeline, plugin_api
from tametools.config import ci_get
from tametools.io import read_tame
from tametools.models import OperationOutput
from tametools.plugin_base import contract, manager
from tametools.plugin_base.base import PLUGIN_REGISTRY, PluginError, register_plugin
from tametools.plugin_base.manager import run_plugin
from tametools.plugin_workflow import passed_on, table_output
from tametools.provenance import log_entries
from tametools.sync import sync_state

SOURCE = Path(tametools.__file__).resolve().parents[1]
ROWS = "name\tvalue\na\t1\na\t4\nb\t2\n"


def tame(extra: str = "") -> str:
    return "<META>\n[INFO]\nDESCRIPTION = \"plugin chaining test\"\n\n[SCALE]\nFACTOR = 10\n" + extra + "</META>\n<DATA>\n" + ROWS + "</DATA>\n"


STATS = """
    import sys
    import pandas as pd
    from tametools.plugin_api import OperationOutput, option, register_plugin, register_view, table_output

    sys.stderr.write(f"[load] stats.py as {__name__}\\n")


    def summarize(df, digits=2):
        values = pd.to_numeric(df["value"], errors="coerce")
        out = values.groupby(df["name"]).mean().round(digits).reset_index()
        out.columns = ["name", "mean"]
        return out


    @register_plugin("MEANS", description="mean per name", version="1.4", output=("name", "mean"))
    def means(dataset, meta, step_name, options):
        return table_output(step_name, summarize(dataset.df, option(options, "ROUND", 2)), result_columns=("mean",))


    @register_plugin("MEANS_TABLE", description="the same numbers, handed back as a table only")
    def means_table(dataset, meta, step_name, options):
        return OperationOutput(name=step_name, table=summarize(dataset.df))


    @register_view("MEANS", title="means")
    def view(dataset):
        return "<!doctype html><html><body>means</body></html>"
"""
SCALE = """
    import pandas as pd
    from tametools.plugin_api import option, register_plugin, require_result, table_output


    @register_plugin("SCALE", description="mean times FACTOR", dependencies=("MEANS>=1.2",))
    def scale(dataset, meta, step_name, options):
        column = require_result(dataset, "MEANS", columns=("mean",))["mean"]
        frame = dataset.df.copy()
        frame["scaled"] = pd.to_numeric(frame[column]) * float(option(options, "FACTOR", 1))
        return table_output(step_name, frame)


    @register_plugin("DOUBLE", description="mean times two, whoever made the table")
    def double(dataset, meta, step_name, options):
        frame = dataset.df.copy()
        if "mean" not in frame.columns:
            raise ValueError("DOUBLE received: " + ", ".join(map(str, frame.columns)))
        frame["double"] = pd.to_numeric(frame["mean"]) * 2
        return table_output(step_name, frame)
"""
COMPOSE = """
    from tametools.plugin_api import OperationOutput, PluginWorkflow, plugin_module, register_plugin, register_view, run_plugin


    @register_plugin("DIRECT", description="runs MEANS the way the guide shows", dependencies=("MEANS",))
    def direct(dataset, meta, step_name, options):
        inner = run_plugin(dataset, "MEANS", meta, "MEANS", {"ROUND": 1})
        return OperationOutput(name=step_name, dataset=inner.dataset, table=inner.table, message="inner ran")


    @register_plugin("TWICE", description="runs MEANS twice and uses its module", dependencies=("MEANS",))
    def twice(dataset, meta, step_name, options):
        run_plugin(dataset, "MEANS", meta, "MEANS", {"ROUND": 1})
        inner = run_plugin(dataset, "MEANS", meta, "MEANS", {"ROUND": 1})
        table = plugin_module("MEANS").summarize(dataset.df)
        return OperationOutput(name=step_name, dataset=inner.dataset, table=table)


    @register_plugin("UNDECLARED", description="runs MEANS without declaring it")
    def undeclared(dataset, meta, step_name, options):
        inner = run_plugin(dataset, "MEANS", meta, "MEANS", {})
        return OperationOutput(name=step_name, dataset=inner.dataset, table=inner.table)


    @register_plugin("FLOW", description="MEANS then SCALE", dependencies=("MEANS", "SCALE"))
    def flow(dataset, meta, step_name, options):
        work = PluginWorkflow(dataset)
        work.plugin("stats", "MEANS")
        work.plugin("scaled", "SCALE", source="stats")
        return work.finish(step_name, primary="scaled")


    @register_view("FLOW", title="flow")
    def view(dataset):
        return "<!doctype html><html><body>flow</body></html>"


    @register_plugin("PING", description="runs PONG")
    def ping(dataset, meta, step_name, options):
        return run_plugin(dataset, "PONG", meta, "PONG", {})


    @register_plugin("PONG", description="runs PING")
    def pong(dataset, meta, step_name, options):
        return run_plugin(dataset, "PING", meta, "PING", {})
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def cli(*args, cwd: Path, plugins: Path | None = None) -> SimpleNamespace:
    """The command line in a fresh process: nothing a test registered, nothing an earlier test loaded."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("TAMETOOLS_")}
    env.update(PYTHONIOENCODING="utf-8", PYTHONPATH=str(SOURCE), TAMETOOLS_DIALOG="off",
               LOCALAPPDATA=str(cwd / "no-user-plugins"), XDG_DATA_HOME=str(cwd / "no-user-plugins"))
    if plugins is not None:
        env["TAMETOOLS_PLUGIN_PATH"] = str(plugins)
    done = subprocess.run([sys.executable, "-m", "tametools", *map(str, args)], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return SimpleNamespace(code=done.returncode, out=done.stdout, err=done.stderr, text=done.stdout + done.stderr)


@pytest.fixture
def lab(tmp_path):
    """A data file and a folder of plugins that use each other."""
    plugins = tmp_path / "plugins"
    write(plugins / "stats.py", STATS)
    write(plugins / "scale.py", SCALE)
    write(plugins / "compose.py", COMPOSE)
    data = tmp_path / "data.tame"
    data.write_text(tame(), encoding="utf-8")
    return SimpleNamespace(dir=tmp_path, data=data, plugins=plugins,
                           run=lambda *args: cli(*args, cwd=tmp_path, plugins=plugins))


@pytest.fixture
def registry():
    """Plugins a test registers are taken out again."""
    manager.list_plugins()
    before = dict(PLUGIN_REGISTRY)
    yield
    PLUGIN_REGISTRY.clear()
    PLUGIN_REGISTRY.update(before)


def last_operation(path: Path) -> dict:
    return log_entries(read_tame(path))[-1].get("EFFECTIVE_PARAMS", {})


# --- what the inner plugin may run -----------------------------------------------------------------------------------

def test_a_plugin_run_by_another_plugin_has_what_the_person_allowed(lab):
    ran = lab.run("run-plugin", lab.data, "DIRECT", "--allow-plugins")           # no allow_external anywhere in the plugin
    assert ran.code == 0 and "inner ran" in ran.out and "2.5" in ran.out, ran.text
    flow = lab.run("run-plugin", lab.data, "FLOW", "--allow-plugins")            # PluginWorkflow.plugin with its defaults
    assert flow.code == 0 and "Unknown workflow plugin" not in flow.text, flow.text
    refused = lab.run("run-plugin", lab.data, "DIRECT")                          # without the person's word nothing from the folder runs
    assert refused.code == 1 and "--allow-plugins" in refused.out


def test_a_plugin_cannot_allow_more_than_the_person_did(lab, registry, monkeypatch):
    dataset = read_tame(lab.data)

    @register_plugin("INNER_OUTSIDE")
    def inner(dataset, meta, step_name, options):
        return OperationOutput(name=step_name, message="inner")

    @register_plugin("OUTER_TRUSTED")
    def outer(dataset, meta, step_name, options):
        found = run_plugin(dataset, "INNER_OUTSIDE", meta, "INNER_OUTSIDE", {}, allow_external=True)
        return OperationOutput(name=step_name, message="inner ran" if found is not None else "inner refused")

    monkeypatch.setitem(manager._TRUSTED_PLUGINS, "OUTER_TRUSTED", PLUGIN_REGISTRY["OUTER_TRUSTED"])
    assert run_plugin(dataset, "OUTER_TRUSTED", {}, "OUTER_TRUSTED", {}).message == "inner refused"
    assert run_plugin(dataset, "OUTER_TRUSTED", {}, "OUTER_TRUSTED", {}, allow_external=True).message == "inner ran"
    assert manager._RUN.get() is None                                            # nothing is left behind after a run


def test_a_plugin_used_without_being_declared_is_pointed_out(lab):
    undeclared = lab.run("run-plugin", lab.data, "UNDECLARED", "--allow-plugins")      # runs, as before, and now says what is missing
    assert undeclared.code == 0 and 'warning: UNDECLARED used MEANS without declaring it' in undeclared.out
    assert 'dependencies=("MEANS",)' in undeclared.out
    for declared in ("DIRECT", "TWICE", "FLOW"):                                       # run_plugin, plugin_module and a workflow
        assert "without declaring it" not in lab.run("run-plugin", lab.data, declared, "--allow-plugins").out


def test_plugins_that_run_each_other_are_stopped_with_their_names(lab):
    ran = lab.run("run-plugin", lab.data, "PING", "--allow-plugins")
    assert ran.code == 1 and "PING -> PONG -> PING" in ran.text
    assert "RecursionError" not in ran.text and len(ran.text.splitlines()) < 12


# --- what is recorded ------------------------------------------------------------------------------------------------

def test_the_log_keeps_the_outer_plugin_and_lists_what_it_ran(lab):
    assert lab.run("run-plugin", lab.data, "TWICE", "--allow-plugins", "--output", "out.tame").code == 0
    effective = last_operation(lab.dir / "out.tame")
    assert effective["PLUGIN_IMPLEMENTATION"]["NAME"] == "TWICE" and len(effective["PLUGIN_IMPLEMENTATION"]["SOURCE_SHA256"]) == 64
    ran, used = effective["PLUGIN_CALLS"]
    assert (ran["NAME"], ran["VERSION"], ran["OPTIONS"], ran["COUNT"]) == ("MEANS", "1.4", {"ROUND": 1}, 2)
    assert (used["NAME"], used["USE"]) == ("MEANS", "module") and len(used["SOURCE_SHA256"]) == 64


def test_a_workflow_step_is_recorded_as_its_own_plugin(lab):
    assert lab.run("run-plugin", lab.data, "FLOW", "--allow-plugins", "--output", "flow.tame").code == 0
    by_step = {entry["OPERATION"]: entry.get("EFFECTIVE_PARAMS", {}) for entry in log_entries(read_tame(lab.dir / "flow.tame"))}
    assert by_step["WORKFLOW:STATS"]["PLUGIN_IMPLEMENTATION"]["NAME"] == "MEANS"
    assert by_step["WORKFLOW:SCALED"]["PLUGIN_IMPLEMENTATION"]["NAME"] == "SCALE"
    assert by_step["CLI:RUN-PLUGIN"]["PLUGIN_IMPLEMENTATION"]["NAME"] == "FLOW" and "PLUGIN_CALLS" not in by_step["CLI:RUN-PLUGIN"]


def test_a_result_names_the_plugin_that_returned_it(lab):
    assert lab.run("run-plugin", lab.data, "FLOW", "--allow-plugins", "--output", "flow.tame", "--view", "flow.html").code == 0
    made = read_tame(lab.dir / "flow.tame").meta["ANALYSIS_RESULT"]
    assert made == {"PLUGIN": "FLOW", "STEP": "scaled", "VIA": ["SCALE"]}        # not the name of the step it ran as
    assert "flow" in (lab.dir / "flow.html").read_text(encoding="utf-8")          # so the screen registered for FLOW is found

    carried = lab.run("run-plugin", lab.data, "DIRECT", "--allow-plugins", "--output", "direct.tame", "--view", "direct.html")
    assert carried.code == 0, carried.text
    assert read_tame(lab.dir / "direct.tame").meta["ANALYSIS_RESULT"] == {"PLUGIN": "DIRECT", "VIEW": "MEANS", "VIA": ["MEANS"]}
    assert "means" in (lab.dir / "direct.html").read_text(encoding="utf-8")       # the screen it had as MEANS's result still opens


def test_a_recorded_result_handed_back_as_it_was_still_verifies(lab, registry):
    from tametools.plugin_workflow import PluginWorkflow
    from tametools.provenance_audit import verify_history

    @register_plugin("INNER_MEANS")
    def inner(dataset, meta, step_name, options):
        return table_output(step_name, pd.DataFrame({"name": ["a"], "mean": [2.5]}))

    @register_plugin("HANDS_BACK")
    def outer(dataset, meta, step_name, options):
        work = PluginWorkflow(dataset)
        work.plugin("stats", "INNER_MEANS")
        return OperationOutput(name=step_name, dataset=work.outputs["stats"].dataset)      # already in the history as INNER_MEANS's

    result = run_plugin(read_tame(lab.data), "HANDS_BACK", {}, "HANDS_BACK", {}, allow_external=True).dataset
    assert result.meta["ANALYSIS_RESULT"] == {"PLUGIN": "HANDS_BACK", "STEP": "stats", "VIA": ["INNER_MEANS"]}
    assert [entry["OPERATION"] for entry in log_entries(result)] == ["WORKFLOW:STATS", "PLUGIN_RESULT"]   # the change is in the history too
    checks = verify_history(result)["checks"]
    assert all(checks[key] == "PASS" for key in ("history", "data", "controls")), checks


# --- what is passed on -----------------------------------------------------------------------------------------------

def test_settings_written_in_the_file_reach_a_plugin_however_it_is_chained(lab):
    (lab.dir / "works.tame").write_text(tame('\n[WORKS]\nDEFAULT = ["MEANS", "SCALE"]\n'), encoding="utf-8")
    chained = lab.run("run", "works.tame", "--allow-plugins")
    composed = lab.run("run-plugin", lab.data, "FLOW", "--allow-plugins")
    assert chained.code == 0 and composed.code == 0, chained.text + composed.text
    for ran in (chained, composed):                                              # [SCALE] FACTOR = 10 in the file, both ways
        assert "25.0" in ran.out and "20.0" in ran.out, ran.out


def test_a_table_is_what_the_next_step_of_a_chain_receives(lab):
    (lab.dir / "works.tame").write_text(tame('\n[WORKS]\nDEFAULT = ["MEANS_TABLE", "DOUBLE"]\n'), encoding="utf-8")
    ran = lab.run("run", "works.tame", "--allow-plugins")
    assert ran.code == 0 and "5.0" in ran.out and "4.0" in ran.out, ran.text
    assert "MEANS_TABLE returned only a table; it was passed on as a dataset" in ran.out

    (lab.dir / "piped.tame").write_text(tame('\n[PIPELINES]\nDEFAULT = ["run-plugin MEANS_TABLE", "run-plugin DOUBLE"]\n'), encoding="utf-8")
    piped = lab.run("run-pipeline", "piped.tame", "--allow-plugins")
    assert piped.code == 0 and "5.0" in piped.out, piped.text


def test_a_table_that_cannot_be_a_dataset_is_said_not_dropped():
    output = passed_on(OperationOutput(name="X", table=pd.DataFrame([[1, 2]], columns=["a", "a"])), "X")
    assert output.dataset is None and "cannot be passed on" in output.warnings[0]
    kept = table_output("X", pd.DataFrame({"a": [1]}))
    assert passed_on(kept, "X") is kept and not kept.warnings                      # a plugin that returned a dataset is left alone


def test_a_work_stops_at_a_step_nobody_knows(lab):
    (lab.dir / "typo.tame").write_text(tame('\n[WORKS]\nDEFAULT = ["MEANS", "SCALLE", "DOUBLE"]\n'), encoding="utf-8")
    ran = lab.run("run", "typo.tame", "--allow-plugins", "--output", "never.tame")
    assert ran.code == 1 and "stopped at 'SCALLE'" in ran.out and "Nothing was saved" in ran.out
    assert "steps run before it: MEANS" in ran.out and not (lab.dir / "never.tame").exists()

    result = pipeline.execute_work(read_tame(lab.dir / "typo.tame").replace(meta={"WORKS": {"DEFAULT": ["VALIDATE", "NO_SUCH_STEP", "DESCRIBE"]}}))
    assert [output.status for output in result.outputs] == ["SUCCEEDED", "SKIPPED"]      # DESCRIBE did not run on data that missed a step
    assert chain.unfinished_steps(result) == ["NO_SUCH_STEP"]

    (lab.dir / "piped.tame").write_text(tame('\n[WORKS]\nDEFAULT = ["SCALLE"]\n\n[PIPELINES]\nDEFAULT = ["run DEFAULT"]\n'), encoding="utf-8")
    piped = lab.run("run-pipeline", "piped.tame", "--allow-plugins")
    assert piped.code == 1 and "SCALLE" in piped.text


def test_the_three_runners_share_one_loop():
    from tametools import actions, command_pipelines
    assert pipeline.run_chain is chain.run_chain is command_pipelines.run_chain
    assert "run_chain(" in Path(actions.__file__).read_text(encoding="utf-8")


# --- how plugin files find each other --------------------------------------------------------------------------------

def test_importing_a_sibling_plugin_file_does_not_run_it_twice(tmp_path):
    plugins = tmp_path / "plugins"
    write(plugins / "stats.py", STATS)
    write(plugins / "uses_stats.py", """
        from stats import summarize                      # a sibling plugin file, imported like a module
        from tametools.plugin_api import OperationOutput, register_plugin

        @register_plugin("USES_STATS", description="uses a function of stats.py")
        def uses_stats(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, table=summarize(dataset.df))
    """)
    write(plugins / "a_first.py", """
        import stats                                     # read before stats.py itself: the import loads it, the loader does not repeat it
        from tametools.plugin_api import OperationOutput, register_plugin

        @register_plugin("A_FIRST", description="imports a plugin file that sorts after it")
        def a_first(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, table=stats.summarize(dataset.df))
    """)
    (tmp_path / "data.tame").write_text(tame(), encoding="utf-8")
    listed = cli("plugins", "--allow-plugins", cwd=tmp_path, plugins=plugins)
    assert listed.code == 0 and listed.err.count("[load] stats.py") == 1, listed.err
    for name, file in (("MEANS 1.4", "stats.py"), ("USES_STATS", "uses_stats.py"), ("A_FIRST", "a_first.py")):
        assert f"{name} [folder {plugins / file}]" in listed.out, listed.out          # each plugin is where it was written
    assert "warning:" not in listed.out
    ran = cli("run-plugin", "data.tame", "USES_STATS", "--allow-plugins", cwd=tmp_path, plugins=plugins)
    assert ran.code == 0 and "2.5" in ran.out and ran.err.count("[load] stats.py") == 1


def test_using_a_function_of_a_bundled_plugin_raises_no_false_alarm(tmp_path):
    plugins = tmp_path / "plugins"
    write(plugins / "uses_bundled.py", """
        from tametools.plugins.westgard_qc.multirule import evaluate_multirule
        from tametools.plugin_api import OperationOutput, plugin_module, register_plugin

        @register_plugin("USES_BUNDLED", description="uses a function of a plugin that comes with tametools")
        def uses_bundled(dataset, meta, step_name, options):
            same = plugin_module("WESTGARD_QC").evaluate_multirule
            return OperationOutput(name=step_name, message=f"imported={callable(evaluate_multirule)} by_name={callable(same)}")
    """)
    (tmp_path / "data.tame").write_text(tame(), encoding="utf-8")
    listed = cli("plugins", "--allow-plugins", cwd=tmp_path, plugins=plugins)
    assert listed.code == 0 and "USES_BUNDLED" in listed.out
    assert "collides" not in listed.text and "not loaded" not in listed.text and "already provided" not in listed.text
    ran = cli("run-plugin", "data.tame", "USES_BUNDLED", "--allow-plugins", cwd=tmp_path, plugins=plugins)
    assert ran.code == 0 and "imported=True by_name=True" in ran.out, ran.text


def test_plugin_module_follows_the_rules_of_running_the_plugin(lab):
    assert lab.run("run-plugin", lab.data, "TWICE", "--allow-plugins").code == 0
    (lab.dir / "chosen.tame").write_text(tame('\n[PLUGINS]\nENABLED = ["USES_MODULE"]\n'), encoding="utf-8")
    write(lab.plugins / "uses_module.py", """
        from tametools.plugin_api import OperationOutput, plugin_module, register_plugin

        @register_plugin("USES_MODULE", description="uses MEANS as a module without declaring it")
        def uses_module(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, table=plugin_module("MEANS").summarize(dataset.df))
    """)
    refused = lab.run("run-plugin", "chosen.tame", "USES_MODULE", "--allow-plugins")
    assert refused.code == 1 and "선택하지 않은 플러그인: MEANS" in refused.text      # as running MEANS would be
    with pytest.raises(PluginError, match="no plugin named NO_SUCH_PLUGIN"):
        plugin_api.plugin_module("NO_SUCH_PLUGIN")


def test_a_sync_watches_the_plugins_that_were_really_run(lab):
    write(lab.dir / "source.csv", "name,value\na,1\na,4\nb,2\n")
    (lab.dir / "project.tame").write_text(
        '<META>\n[SYNC]\nSOURCE = "source.csv"\nWORK = "MEANS"\nRESULT = "result.tame"\n\n[WORKS]\nMEANS = ["UNDECLARED"]\n</META>\n'
        "<DATA>\nname\tvalue\n</DATA>\n", encoding="utf-8")
    first = lab.run("sync", "project.tame", "--allow-plugins")
    assert first.code == 0, first.text
    recorded = sync_state(read_tame(lab.dir / "project.tame"))["PLUGIN_SHA256"]
    assert sorted(recorded) == ["MEANS", "UNDECLARED"]                            # MEANS is nowhere in the file and not declared
    assert "UNCHANGED" in lab.run("sync", "project.tame", "--allow-plugins").out.upper() or \
        "변경 없음" in lab.run("sync", "project.tame", "--allow-plugins").out

    stats = lab.plugins / "stats.py"
    stats.write_text(stats.read_text(encoding="utf-8").replace("digits=2", "digits=3"), encoding="utf-8")
    again = lab.run("sync", "project.tame", "--allow-plugins")
    assert again.code == 0 and "플러그인 구현이 마지막 동기화 이후 바뀌었습니다: MEANS" in again.out, again.text
    assert sync_state(read_tame(lab.dir / "project.tame"))["PLUGIN_SHA256"]["MEANS"] != recorded["MEANS"]


def test_a_file_imported_like_a_module_is_part_of_the_plugin_that_imports_it(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    write(plugins / "stats.py", STATS.replace('sys.stderr.write(f"[load] stats.py as {__name__}\\n")', ""))
    write(plugins / "uses_stats.py", """
        from stats import summarize
        from tametools.plugin_api import OperationOutput, register_plugin

        @register_plugin("USES_STATS_HASHED", description="uses a function of stats.py")
        def uses_stats(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, table=summarize(dataset.df))
    """)
    probe = ("import os, sys\nfrom pathlib import Path\nfrom tametools.plugin_base.manager import plugin_sources\n"
             "target = Path(os.environ['TAMETOOLS_PLUGIN_PATH']) / 'stats.py'\n"
             "before = plugin_sources(['USES_STATS_HASHED'], None, allow_external=True)\n"
             "target.write_text(target.read_text(encoding='utf-8') + '\\n# changed\\n', encoding='utf-8')\n"
             "print(sorted(before), before != plugin_sources(['USES_STATS_HASHED'], None, allow_external=True))\n")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(SOURCE), TAMETOOLS_PLUGIN_PATH=str(plugins),
               LOCALAPPDATA=str(tmp_path / "none"), XDG_DATA_HOME=str(tmp_path / "none"))
    done = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.stdout.strip() == "['USES_STATS_HASHED'] True", done.stdout + done.stderr


# --- names --------------------------------------------------------------------------------------------------------------

def test_a_plugin_cannot_take_a_name_tametools_uses(registry):
    for name in ("INFO", "column", "VALIDATE", "sync-state", "LOG"):
        with pytest.raises(PluginError, match="taken by tametools itself"):
            register_plugin(name)
    assert not meta_sections.RESERVED_NAMES.intersection(spec.name for spec in manager.list_plugins())


def test_the_reserved_names_are_the_ones_the_code_uses():
    from tametools import sync
    steps = {name for handler in pipeline.STEP_HANDLERS for name in getattr(handler, "names", ())}
    assert steps == set(meta_sections.STEP_NAMES)
    assert {sync.SECTION, sync.STATE_SECTION, sync.RESULT_SECTION, sync.RECORDS_SECTION, *sync.MANAGED_SECTIONS} <= set(meta_sections.CORE_SECTIONS)
    assert set(meta_sections.CORE_SECTIONS.values()) == {"file", "work", "state", "result", "web"}


def test_validate_points_out_a_settings_section_that_is_one_letter_off(lab):
    (lab.dir / "typo.tame").write_text(tame().replace("[SCALE]", "[SCALEE]") + "", encoding="utf-8")
    found = lab.run("validate", "typo.tame")
    assert found.code == 0 and "warning: META[SCALEE] is not read" in found.out and "Did you mean [SCALE]?" in found.out
    (lab.dir / "bundled.tame").write_text(tame('\n[WESTGARD_QCC]\nRULES = ["1_3s"]\n\n[MY_NOTES]\nTEXT = "kept by the lab"\n'), encoding="utf-8")
    bundled = lab.run("validate", "bundled.tame")
    assert "Did you mean [WESTGARD_QC]?" in bundled.out and "MY_NOTES" not in bundled.out      # a section of the file's own is left alone
    assert "warning: META" not in lab.run("validate", lab.data).out

    aliased = {"WORKS": {"DEFAULT": ["SUMMARYY"]}, "SUMMARYY": {"MODE": "X"}, "REPORT_STEP": {"PLUGIN": "SCALE"}}
    assert meta_sections.section_typos(aliased, ["SUMMARY", "REPORT_STEPS"]) == []    # the settings of a declared step are known


# --- what plugins promise each other ---------------------------------------------------------------------------------

def test_versions_are_compared_the_way_people_read_them():
    key = contract.version_key
    assert key("1.10") > key("1.9") and key("1.2") == key("1.2.0") == key("v1.2") and key("2") > key("1.99.99")
    assert key("") < key("0.1") and key("1.2.1") > key("1.2")


def test_a_dependency_that_is_too_old_is_said_before_anything_runs(lab):
    scale = lab.plugins / "scale.py"
    scale.write_text(scale.read_text(encoding="utf-8").replace("MEANS>=1.2", "MEANS>=2.0"), encoding="utf-8")
    (lab.dir / "works.tame").write_text(tame('\n[WORKS]\nDEFAULT = ["MEANS", "SCALE"]\n'), encoding="utf-8")
    ran = lab.run("run", "works.tame", "--allow-plugins")
    assert ran.code == 1 and "SCALE needs MEANS 2.0 or later, but MEANS 1.4 is here" in ran.text and str(lab.plugins / "stats.py") in ran.text
    listed = lab.run("plugins", "--allow-plugins")
    assert f"MEANS 1.4 [folder {lab.plugins / 'stats.py'}]" in listed.out and "\nSCALE [folder" in listed.out


def test_a_dependency_is_a_name_with_an_optional_lowest_version(registry):
    @register_plugin("NEEDS_TWO", dependencies=("means >= 1.2", "Qc-Analysis"))
    def needs_two(dataset, meta, step_name, options):
        return OperationOutput(name=step_name)

    spec = PLUGIN_REGISTRY["NEEDS_TWO"]
    assert spec.dependencies == ("MEANS", "QC_ANALYSIS") and spec.requires == (("MEANS", "1.2"),)
    for wrong in (("MEANS==1.2",), ("",), "MEANS"):
        with pytest.raises(PluginError):
            register_plugin("WRONG_DEPENDENCY", dependencies=wrong)


def test_a_plugin_that_declares_its_columns_must_return_them(lab, registry):
    dataset = read_tame(lab.data)

    @register_plugin("KEEPS_ITS_WORD", output=("name", "total"))
    def keeps(dataset, meta, step_name, options):
        return table_output(step_name, pd.DataFrame({"name": ["a"], "total": [1]}))

    @register_plugin("BREAKS_ITS_WORD", output=("name", "total"))
    def breaks(dataset, meta, step_name, options):
        return table_output(step_name, pd.DataFrame({"name": ["a"], "sum": [1]}))

    assert run_plugin(dataset, "KEEPS_ITS_WORD", {}, "KEEPS_ITS_WORD", {}, allow_external=True).dataset is not None
    with pytest.raises(PluginError, match="declares the output columns name, total, but its result has no total"):
        run_plugin(dataset, "BREAKS_ITS_WORD", {}, "BREAKS_ITS_WORD", {}, allow_external=True)


def test_require_result_says_what_was_expected_and_what_arrived(lab):
    result = table_output("MEANS", pd.DataFrame({"name": ["a"], "mean": [2.5]})).dataset
    result.meta["COLUMN"]["mean"]["ID"] = "average"
    assert contract.require_result(result, "means", columns=("name", "average")) == {"name": "name", "average": "mean"}
    with pytest.raises(PluginError, match="has no column median"):
        contract.require_result(result, "MEANS", columns=("median",))
    with pytest.raises(PluginError, match="needs the result of SCALE, but the data it received is a result of MEANS"):
        contract.require_result(result, "SCALE")
    with pytest.raises(PluginError, match="not marked as any plugin's result"):
        contract.require_result(read_tame(lab.data), "MEANS")
    carried = result.replace(meta={**result.meta, "ANALYSIS_RESULT": {"PLUGIN": "OUTER", "VIA": ["MEANS"]}})
    assert contract.require_result(carried, "MEANS", columns=("mean",)) == {"mean": "mean"}      # a result carried on is still that result
    legacy = result.replace(meta={"PLUGIN": {"NAME": "QC_ANALYSIS", "MODE": "WESTGARD"}})
    assert contract.result_plugins(legacy) == ["QC_ANALYSIS"]

    wrong = lab.run("run-plugin", lab.data, "SCALE", "--allow-plugins")              # SCALE given raw data instead of MEANS's result
    assert wrong.code == 1 and "needs the result of MEANS" in wrong.text and "Traceback" not in wrong.text


def test_claiming_a_result_keeps_what_stood_there():
    spec = SimpleNamespace(name="OUTER")
    step = table_output("review", pd.DataFrame({"a": [1]})).dataset
    assert contract.claim_result(step, spec, same=["OUTER"], is_plugin=lambda name: False, has_view=lambda name: False).meta["ANALYSIS_RESULT"] \
        == {"PLUGIN": "OUTER", "STEP": "review"}
    own = table_output("OUTER", pd.DataFrame({"a": [1]})).dataset
    assert contract.claim_result(own, spec, same=["OUTER", "ALIAS"], is_plugin=lambda name: True, has_view=lambda name: False) is own
    unmarked = own.replace(meta={})
    assert contract.claim_result(unmarked, spec, same=["OUTER"], is_plugin=lambda name: True, has_view=lambda name: False) is unmarked
    inner = table_output("INNER", pd.DataFrame({"a": [1]})).dataset
    inner.meta["ANALYSIS_RESULT"].update(ROW_KEY=["a"], VIA=["DEEPER"])
    claimed = contract.claim_result(inner, spec, same=["OUTER"], is_plugin=lambda name: True, has_view=lambda name: name == "INNER")
    assert claimed.meta["ANALYSIS_RESULT"] == {"PLUGIN": "OUTER", "ROW_KEY": ["a"], "VIEW": "INNER", "VIA": ["INNER", "DEEPER"]}
    assert ci_get(inner.meta, "ANALYSIS_RESULT")["PLUGIN"] == "INNER"                 # the dataset that was passed in is not changed
