"""Writing a plugin from outside tametools: the public API, what a mistake says, where plugins may live, what they may add.

The scenarios that need a clean plugin registry (a plugin folder, a pip-installed plugin) run the real command line
in a process of their own.
"""
import dataclasses
import importlib
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import tametools
from tametools import dialogs, plugin_api
from tametools.dialogs import field, form
from tametools.io import read_tame
from tametools.models import OperationOutput
from tametools.plugin_base import manager
from tametools.plugin_base.base import PLUGIN_API_VERSION, PLUGIN_REGISTRY, PluginError, register_plugin
from tametools.exporters import export_dataset, export_frame, plugin_export_formats
from tametools.plugin_base.extensions import (COMMAND_REGISTRY, EXPORTER_REGISTRY, VIEW_REGISTRY, register_command, register_exporter,
                                              register_view, render_view, view_for)
from tametools.plugin_base.options import option
from tametools.sync import sync_project, sync_state

SOURCE = Path(tametools.__file__).resolve().parents[1]
DATA = ("<META>\n[INFO]\nDESCRIPTION = \"plugin api test\"\n\n[HELLO]\nN = 2\n</META>\n<DATA>\n"
        "name\tvalue\na\t1\nb\t2\nc\t3\n</DATA>\n")
HELLO = """
    from tametools.plugin_api import OperationOutput, option, register_plugin

    @register_plugin("HELLO", description="first rows")
    def hello(dataset, meta, step_name, options):
        n = option(options, "N", 2)
        return OperationOutput(name=step_name, table=dataset.df.head(n), message=f"hello N={n!r}")
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def tametools_cli(*args, cwd: Path, plugins: Path | None = None, **environment) -> SimpleNamespace:
    """The command line in a fresh process: nothing a test registered, nothing an earlier test loaded."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("TAMETOOLS_")}
    env.update(PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join([str(SOURCE), *filter(None, [environment.pop("PYTHONPATH", "")])]),
               LOCALAPPDATA=str(cwd / "no-user-plugins"), XDG_DATA_HOME=str(cwd / "no-user-plugins"), **environment)
    if plugins is not None:
        env["TAMETOOLS_PLUGIN_PATH"] = str(plugins)
    done = subprocess.run([sys.executable, "-m", "tametools", *map(str, args)], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return SimpleNamespace(code=done.returncode, out=done.stdout, err=done.stderr, text=done.stdout + done.stderr)


@pytest.fixture
def work(tmp_path):
    write(tmp_path / "data.tame", DATA)
    return SimpleNamespace(dir=tmp_path, data=tmp_path / "data.tame", plugins=tmp_path / "plugins")


@pytest.fixture
def registry():
    """Plugins, commands and views a test registers are taken out again."""
    manager.list_plugins()          # the bundled plugins first, or putting the registries back would take them out too
    before = dict(PLUGIN_REGISTRY), dict(COMMAND_REGISTRY), dict(VIEW_REGISTRY), dict(EXPORTER_REGISTRY)
    yield
    for current, saved in zip((PLUGIN_REGISTRY, COMMAND_REGISTRY, VIEW_REGISTRY, EXPORTER_REGISTRY), before):
        current.clear()
        current.update(saved)


# --- the public API --------------------------------------------------------------------------------------------------

def test_everything_a_plugin_needs_comes_from_one_module():
    for name in plugin_api.__all__:
        assert getattr(plugin_api, name) is not None, name
    assert plugin_api.PLUGIN_API_VERSION == PLUGIN_API_VERSION == 2
    assert plugin_api.register_plugin is register_plugin and plugin_api.OperationOutput is OperationOutput
    assert plugin_api.form is form and plugin_api.option is option
    with pytest.raises(AttributeError, match=f"tametools.plugin_api has no 'sync_project'. The plugin API \\(version {PLUGIN_API_VERSION}\\) is: "):
        plugin_api.sync_project
    from tametools.plugin_base import register_plugin as guessed       # the import an author guesses first
    assert guessed is register_plugin


def test_the_bundled_qc_plugin_is_written_against_the_public_api_only():
    package = SOURCE / "tametools" / "plugins" / "westgard_qc"
    assert {path.name for path in package.glob("*.py")} == {"__init__.py", "actions.py", "commands.py", "multirule.py", "report.py"}
    for path in package.glob("*.py"):
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip().startswith(("from tametools", "import tametools", "from ..", "from .."))]
        assert all(line.startswith("from tametools.plugin_api import") for line in lines), (path.name, lines)
    for gone in ("qc_multirule", "qc_report", "qc_actions"):            # nothing about QC is left in the core
        assert not (SOURCE / "tametools" / f"{gone}.py").exists()
    cli_text = (SOURCE / "tametools" / "cli.py").read_text(encoding="utf-8")
    assert "qc_actions" not in cli_text and 'args.command == "qc-action"' not in cli_text


def test_the_core_does_not_import_plugin_modules():
    """The core calls plugins by name only, so a plugin file can be removed or replaced without touching the core."""
    package = SOURCE / "tametools"
    offenders = []
    for path in package.rglob("*.py"):
        if package / "plugins" in path.parents:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\b(from|import)\s+(tametools)?\.?plugins(\.|\s+import)", code):
                offenders.append(f"{path.relative_to(package)}:{number}: {line.strip()}")
    assert offenders == []


def test_method_comparison_regressions_live_in_the_core():
    from tametools.analysis_contract import resolve_selector
    from tametools.method_comparison import deming, passing_bablok

    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert passing_bablok(x, 2 * x + 1) == pytest.approx((2.0, 1.0)) and deming(x, 2 * x + 1) == pytest.approx((2.0, 1.0))
    assert deming(x, 0.5 * x, lam=4.0) == pytest.approx((0.5, 0.0))
    assert all(np.isnan(value) for value in (*passing_bablok(np.ones(5), x), *deming(np.ones(5), x)))     # no spread in x: no line
    dataset = SimpleNamespace(columns=[SimpleNamespace(name="검체"), SimpleNamespace(name="방법")],
                              columns_with_tag=lambda tag: [SimpleNamespace(name="방법")] if tag == "METHOD" else [])
    assert resolve_selector(dataset, " 검체 ").name == "검체" and resolve_selector(dataset, "tag:METHOD").name == "방법"
    assert resolve_selector(dataset, "") is None
    for wrong, message in (("없는열", "Unknown column selector: 없는열"), ("tag:RESULT", "Unknown or ambiguous column selector: tag:RESULT")):
        with pytest.raises(ValueError, match=message):
            resolve_selector(dataset, wrong)


def test_a_plugin_declares_the_host_screen_it_uses_and_the_plugin_that_replaces_it(registry):
    screen = manager.panel_plugin("reference_interval")
    assert screen.name == "RI_EP28" and manager.same_plugin_names("RI_EP28") == ["REFERENCE_INTERVAL_EP28", "RI_EP28"]
    assert manager.panel_plugin("Reference_Interval ") is screen and manager.panel_plugin("") is None and manager.panel_plugin("no_such_screen") is None
    assert {spec.name: spec.superseded_by for spec in manager.list_plugins() if spec.superseded_by} == {"REFERENCE_INTERVAL": "RI_EP28", "RI": "RI_EP28"}
    assert all(not spec.panel for spec in manager.list_plugins() if spec.name != "RI_EP28")
    assert manager.same_plugin_names("NOT_THERE") == []

    handler = lambda dataset, meta, step_name, options: None
    register_plugin("lab-ri", panel=" Reference_Interval ", superseded_by="ri-ep28")(handler)
    register_plugin("LAB_QC_BOARD", panel="qc_board")(handler)
    assert (PLUGIN_REGISTRY["LAB_RI"].panel, PLUGIN_REGISTRY["LAB_RI"].superseded_by) == ("reference_interval", "RI_EP28")
    # A plugin from outside cannot take a screen away from a bundled one, and its own screens count only when allowed.
    assert manager.panel_plugin("reference_interval", allow_external=True) is screen
    assert manager.panel_plugin("qc_board") is None and manager.panel_plugin("qc_board", allow_external=True).name == "LAB_QC_BOARD"


def test_a_plugin_made_for_a_newer_api_is_refused_when_it_registers(registry):
    with pytest.raises(PluginError, match=f"Plugin FUTURE needs plugin API {PLUGIN_API_VERSION + 1}, but this tametools provides {PLUGIN_API_VERSION}. Upgrade tametools"):
        register_plugin("FUTURE", api=PLUGIN_API_VERSION + 1)
    for wrong in (0, True, "1"):
        with pytest.raises(PluginError, match="api must be a plugin API version"):
            register_plugin("X", api=wrong)
    with pytest.raises(PluginError, match="needs a plugin name"):
        register_plugin(" ")
    register_plugin("CURRENT", api=1)(lambda dataset, meta, step_name, options: None)
    assert PLUGIN_REGISTRY["CURRENT"].api == 1 and "FUTURE" not in PLUGIN_REGISTRY


def test_option_reads_one_type_whether_the_value_came_typed_or_as_text():
    typed, text = {"N": 5, "RATE": 0.5, "FLAG": True, "RULES": ["1_3s", "2_2s"], "MODE": "mean"}, \
                  {"n": "5", "rate": "0.5", "flag": "true", "rules": "1_3s, 2_2s", "mode": " mean "}
    for options in (typed, text):
        assert (option(options, "N", 0), option(options, "RATE", 1.0), option(options, "FLAG", False),
                option(options, "RULES", []), option(options, "MODE", "x")) == (5, 0.5, True, ["1_3s", "2_2s"], "mean")
    assert option({"FLAG": "no"}, "flag", True) is False and option({"N": 3.0}, "N", kind=int) == 3
    assert option({}, "N", 7) == 7 and option({"N": " "}, "N", 7) == 7 and option(None, "N") is None
    assert option({"X": [1, 2]}, "X") == [1, 2]                         # no default and no kind: as it is
    assert option({"MODE": "median"}, "MODE", "mean", choices=["mean", "median"]) == "median"
    for options, arguments, message in [
            ({"N": "abc"}, dict(default=0), "Option N must be a whole number: 'abc'"),
            ({"N": 1.5}, dict(kind=int), "Option N must be a whole number: 1.5"),
            ({"N": True}, dict(kind=int), "Option N must be a whole number: True"),
            ({"RATE": "nan"}, dict(default=1.0), "Option RATE must be a number: 'nan'"),
            ({"FLAG": "maybe"}, dict(default=False), "Option FLAG must be true or false: 'maybe'"),
            ({"MODE": ["a"]}, dict(default="x"), "Option MODE must be text"),
            ({"MODE": "mode"}, dict(default="mean", choices=["mean", "median"]), "Option MODE must be one of mean, median: 'mode'")]:
        with pytest.raises(PluginError, match=message.replace("(", "\\(")):
            option(options, next(iter(options)), **arguments)
    with pytest.raises(PluginError, match="kind must be str, int, float, bool or list"):
        option({"N": 1}, "N", kind=dict)


# --- what a mistake says ---------------------------------------------------------------------------------------------

def test_a_handler_that_returns_nothing_or_the_wrong_thing_is_told_so(work, registry):
    register_plugin("NO_RETURN")(lambda dataset, meta, step_name, options: None)
    register_plugin("RAW_TABLE")(lambda dataset, meta, step_name, options: dataset.df)
    dataset = read_tame(work.data)
    with pytest.raises(PluginError, match="Plugin NO_RETURN must return an OperationOutput, but its handler returned nothing "
                                          "\\(is a `return` missing\\?\\). To hand back a table: return OperationOutput"):
        manager.run_plugin(dataset, "NO_RETURN", dataset.meta, "NO_RETURN", {}, allow_external=True)
    with pytest.raises(PluginError, match="Plugin RAW_TABLE must return an OperationOutput, but its handler returned DataFrame"):
        manager.run_plugin(dataset, "RAW_TABLE", dataset.meta, "RAW_TABLE", {}, allow_external=True)
    assert manager.run_plugin(dataset, "NOT_THERE", dataset.meta, "NOT_THERE", {}, allow_external=True) is None   # only this is "unknown"


def test_the_command_line_says_why_a_plugin_is_not_there(work):
    write(work.plugins / "hello.py", HELLO)
    write(work.plugins / "needs_lib.py", "import a_library_that_is_not_installed\n")
    forgotten = tametools_cli("run-plugin", work.data, "HELLO", cwd=work.dir, plugins=work.plugins)
    assert forgotten.code == 1 and "error: unknown plugin: HELLO" in forgotten.out
    assert f"hint: HELLO is defined in {work.plugins / 'hello.py'}, which is outside the trusted plugin folders. Re-run with --allow-plugins" in forgotten.out
    listed = tametools_cli("plugins", work.data, cwd=work.dir, plugins=work.plugins)
    assert "HELLO" not in listed.out.split("hint:")[0] and f"hint: plugins outside the trusted folders were not loaded ({work.plugins})" in listed.out
    broken = tametools_cli("run-plugin", work.data, "NEEDS_LIB", "--allow-plugins", cwd=work.dir, plugins=work.plugins)
    assert broken.code == 1 and "error: unknown plugin: NEEDS_LIB" in broken.out
    assert "hint: a plugin file was not loaded: needs_lib.py: ModuleNotFoundError: No module named 'a_library_that_is_not_installed'" in broken.out
    allowed = tametools_cli("plugins", work.data, "--allow-plugins", cwd=work.dir, plugins=work.plugins)
    assert f"HELLO [folder {work.plugins / 'hello.py'}]: first rows" in allowed.out
    assert "warning: drop-in plugin not loaded: needs_lib.py: ModuleNotFoundError" in allowed.out


def test_the_command_line_reports_a_broken_contract_a_taken_name_and_saves_a_table(work):
    write(work.plugins / "hello.py", HELLO)
    write(work.plugins / "mistakes.py", """
        from tametools.plugin_api import OperationOutput, register_plugin

        @register_plugin("NO_RETURN")
        def no_return(dataset, meta, step_name, options):
            OperationOutput(name=step_name, table=dataset.df)

        @register_plugin("NOTHING")
        def nothing(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, message="no table, no dataset")

        @register_plugin("RI", description="my own RI")
        def my_ri(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, message="my RI ran")
    """)
    missing = tametools_cli("run-plugin", work.data, "NO_RETURN", "--allow-plugins", cwd=work.dir, plugins=work.plugins)
    assert missing.code == 1 and "unknown plugin" not in missing.text and "Traceback" not in missing.text
    assert "Error: a plugin does not keep the plugin contract. Plugin NO_RETURN must return an OperationOutput, but its handler returned nothing" in missing.err

    saved = tametools_cli("run-plugin", work.data, "HELLO", "--allow-plugins", "--option", "N=1", "--output", "out.tame", cwd=work.dir, plugins=work.plugins)
    assert saved.code == 0 and "hello N=1" in saved.out          # the text "1" from the command line was read as a number
    assert "saved: out.tame (from the plugin's table; column tags were inferred)" in saved.out
    assert list(read_tame(work.dir / "out.tame").df["name"]) == ["a"]
    empty = tametools_cli("run-plugin", work.data, "NOTHING", "--allow-plugins", "--output", "none.tame", cwd=work.dir, plugins=work.plugins)
    assert empty.code == 0 and "warning: --output was not written: the plugin returned neither a dataset nor a table." in empty.out
    assert not (work.dir / "none.tame").exists()

    taken = tametools_cli("run-plugin", work.data, "RI", "--allow-plugins", cwd=work.dir, plugins=work.plugins)
    assert "warning: mistakes.py also registers RI and was ignored; the plugin that came with tametools ran." in taken.out
    assert "my RI ran" not in taken.out


# --- where a plugin may live -----------------------------------------------------------------------------------------

PACKAGE_INIT = """
    from tametools.plugin_api import OperationOutput, register_command, register_plugin, register_view, table_output

    from .words import shout

    @register_plugin("PACKED", description="a plugin kept in a folder")
    def packed(dataset, meta, step_name, options):
        return table_output(step_name, dataset.df.head(1), message=shout("packed ok"))

    def _arguments(parser):
        parser.add_argument("word")
        parser.add_argument("--twice", action="store_true")

    @register_command("shout", help="Say a word loudly.", configure=_arguments)
    def run(args):
        print(shout(args.word) * (2 if args.twice else 1) + (" (external allowed)" if args.allow_plugins else ""))
        return 0

    @register_view("PACKED", title="Packed rows")
    def view(dataset):
        return "<!doctype html><html><body><h1>" + str(len(dataset.df)) + " row(s)</h1></body></html>"
"""


def test_a_plugin_can_be_a_folder_and_add_a_command_and_a_screen(work):
    write(work.plugins / "packed" / "__init__.py", PACKAGE_INIT)
    write(work.plugins / "packed" / "words.py", "def shout(text):\n    return text.upper() + '!'\n")
    write(work.plugins / "examples" / "not_loaded.py", "raise RuntimeError('a folder without __init__.py is not a plugin')\n")
    write(work.plugins / "_private" / "__init__.py", "raise RuntimeError('names starting with _ are helpers')\n")

    listed = tametools_cli("plugins", work.data, "--allow-plugins", cwd=work.dir, plugins=work.plugins)
    assert f"PACKED [folder {work.plugins / 'packed'}]: a plugin kept in a folder" in listed.out and "not loaded" not in listed.out
    assert "plugin commands: qc-action, qc-review, shout" in listed.out
    ran = tametools_cli("run-plugin", work.data, "PACKED", "--allow-plugins", "--view", "view.html", cwd=work.dir, plugins=work.plugins)
    assert ran.code == 0 and "PACKED OK!" in ran.out and "view: view.html (Packed rows)" in ran.out
    assert (work.dir / "view.html").read_text(encoding="utf-8") == "<!doctype html><html><body><h1>1 row(s)</h1></body></html>"

    said = tametools_cli("shout", "hello", "--twice", "--allow-plugins", cwd=work.dir, plugins=work.plugins)
    assert (said.code, said.out.strip()) == (0, "HELLO!HELLO! (external allowed)")
    helped = tametools_cli("help", "shout", "--allow-plugins", cwd=work.dir, plugins=work.plugins)
    assert helped.code == 0 and "usage: tametools shout" in helped.out and "--twice" in helped.out
    refused = tametools_cli("shout", "hello", cwd=work.dir, plugins=work.plugins)       # an external plugin's command needs the same trust
    assert refused.code == 2 and "Error: unknown command: shout" in refused.err
    assert f"hint: plugins outside the trusted folders were not loaded ({work.plugins})" in refused.err
    builtin = tametools_cli("qc-action", "--help", cwd=work.dir)                        # the bundled QC plugin's command needs none
    assert builtin.code == 0 and "usage: tametools qc-action" in builtin.out


def test_a_plugin_installed_with_pip_is_found_through_its_entry_point(work):
    site = work.dir / "site"
    write(site / "lab_plugins" / "__init__.py", "")
    write(site / "lab_plugins" / "counts.py", """
        from tametools.plugin_api import OperationOutput, register_plugin

        @register_plugin("LAB_COUNT", description="rows, from an installed package")
        def lab_count(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, message=f"lab_count rows={len(dataset.df)}")

        @register_plugin("RI", description="an installed package cannot take a bundled name")
        def my_ri(dataset, meta, step_name, options):
            return OperationOutput(name=step_name, message="installed RI ran")
    """)
    write(site / "lab_plugins-1.2.3.dist-info" / "METADATA", "Metadata-Version: 2.1\nName: lab-plugins\nVersion: 1.2.3\n")
    write(site / "lab_plugins-1.2.3.dist-info" / "entry_points.txt", "[tametools.plugins]\ncounts = lab_plugins.counts\n")

    listed = tametools_cli("plugins", work.data, cwd=work.dir, PYTHONPATH=str(site))    # no --allow-plugins
    assert "LAB_COUNT [installed lab-plugins 1.2.3]: rows, from an installed package" in listed.out
    assert "RI: Alias of the reference interval plugin." in listed.out
    assert "warning: drop-in plugin not loaded: lab-plugins 1.2.3: plugin name 'RI' collides with a built-in plugin and was ignored." in listed.out
    ran = tametools_cli("run-plugin", work.data, "LAB_COUNT", "--output", "out.tame", cwd=work.dir, PYTHONPATH=str(site))
    assert ran.code == 0 and "lab_count rows=3" in ran.out
    off = tametools_cli("run-plugin", work.data, "LAB_COUNT", cwd=work.dir, PYTHONPATH=str(site), TAMETOOLS_INSTALLED_PLUGINS="off")
    assert off.code == 1 and "error: unknown plugin: LAB_COUNT" in off.out


# --- options, commands and screens ---------------------------------------------------------------------------------------

def test_declared_questions_give_command_line_options_their_type():
    made = form("t", [field("MIN_N", "최소 자료 수", "integer", minimum=1), field("RATE", "비율", "number"), field("FLAG", "사용", "check"),
                      field("RULES", "규칙", "multi", choices=["1_3s", "2_2s"]), field("MODE", "방식", "choice", choices=[("MEAN", "평균"), ("MEDIAN", "중앙값")]),
                      field("DAY", "기준일", "date"), field("NOTE", "메모")])
    text = {"min_n": "3", "RATE": "0.5", "FLAG": "yes", "RULES": "1_3s, 9x", "MODE": "중앙값", "DAY": "2026-11-01", "NOTE": "7", "OTHER": "1"}
    assert dialogs.typed(made, text) == {"min_n": 3, "RATE": 0.5, "FLAG": True, "RULES": ["1_3s", "9x"], "MODE": "MEDIAN",
                                         "DAY": "2026-11-01", "NOTE": "7", "OTHER": "1"}
    already = {"MIN_N": 3, "FLAG": False, "RULES": ["2_2s"], "MODE": "custom", "RATE": ""}
    assert dialogs.typed(made, already) == already                       # typed values and unknown choices are left alone
    for options, message in [({"MIN_N": "many"}, "옵션 MIN_N: '최소 자료 수' 에는 정수를 적어 주세요: many"),
                             ({"MIN_N": "0"}, "옵션 MIN_N: '최소 자료 수' 은\\(는\\) 1 이상이어야 합니다"),
                             ({"FLAG": "maybe"}, "옵션 FLAG: '사용' 은\\(는\\) true 또는 false 여야 합니다: maybe"),
                             ({"DAY": "11/01"}, "옵션 DAY: '기준일' 은\\(는\\) YYYY-MM-DD 형식의 날짜여야 합니다")]:
        with pytest.raises(ValueError, match=message):
            dialogs.typed(made, options)


def test_commands_and_views_are_checked_when_they_are_registered(registry):
    for wrong in ("shout it", "-x", "1st", "lab_report", ""):
        with pytest.raises(PluginError, match="Command name must be lower-case words joined by hyphens"):
            register_command(wrong)
    register_command(" Lab-Summary ")(lambda args: 0)                   # case and outer blanks are not part of the name
    assert "lab-summary" in COMMAND_REGISTRY
    with pytest.raises(PluginError, match="goes on the function that runs it"):
        register_command("ok-name")("not a function")
    with pytest.raises(PluginError, match="needs the name of the plugin"):
        register_view(" ")
    register_command("lab-report", help="h")(lambda args: 0)
    assert COMMAND_REGISTRY["lab-report"].help == "h" and COMMAND_REGISTRY["lab-report"].description == "h"

    register_view("my-plugin", title="화면")(lambda dataset: f"<html>{len(dataset.df)}</html>")
    register_view("BROKEN")(lambda dataset: 3)
    result = SimpleNamespace(df=pd.DataFrame({"a": [1, 2]}), meta={"ANALYSIS_RESULT": {"PLUGIN": "MY_PLUGIN"}})
    assert view_for(result).title == "화면" and render_view(result) == ("화면", "<html>2</html>")
    assert render_view(SimpleNamespace(df=result.df, meta={"ANALYSIS_RESULT": {"PLUGIN": "OTHER", "VIEW": "my_plugin"}}))[0] == "화면"
    assert render_view(SimpleNamespace(df=result.df, meta={})) is None and view_for(SimpleNamespace(meta={"ANALYSIS_RESULT": "x"})) is None
    with pytest.raises(PluginError, match="The view of BROKEN must return an HTML document as text"):
        render_view(SimpleNamespace(df=result.df, meta={"ANALYSIS_RESULT": {"PLUGIN": "BROKEN"}}))


def qc_project(folder: Path) -> Path:
    """The tutorial's QC project (twenty days of control measurements), synced once in ``folder``."""
    tutorial = Path(tametools.__file__).resolve().parents[3] / "tutorial" / "26_qc_sync"
    spec = importlib.util.spec_from_file_location("qc_demo_data", tutorial / "qc_demo_data.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    project = folder / "qc_chem.tame"
    project.write_bytes((tutorial / "qc_chem.tame").read_bytes())
    demo.write_workbook(folder / "qc_chem_daily.xlsx")
    assert sync_project(project, now="2026-10-31T09:00:00+09:00").status == "COMMITTED"
    return project


def test_the_qc_plugin_registers_its_commands_and_its_screen(tmp_path):
    assert {"qc-action", "qc-review"} <= set(manager.plugin_commands())
    project = read_tame(qc_project(tmp_path))
    output = manager.run_plugin(project, "WESTGARD_QC", project.meta, "WESTGARD_QC", dict(project.meta["WESTGARD_QC"]))
    spec = manager.plugin_view(output.dataset)
    assert (spec.name, spec.title) == ("WESTGARD_QC", "QC 보고서")
    title, html = render_view(output.dataset)
    report = (tmp_path / "qc_chem_report.html").read_text(encoding="utf-8")
    charts = html.count("<svg class='lj'")
    assert html.startswith("<!doctype html>") and charts >= 6 and charts == report.count("<svg class='lj'")    # the charts of the sync report
    assert title == "QC 보고서" and "Levey-Jennings 관리도" in html
    assert "4. 경고·거부 목록</h2>" in html and "조치 기록 없음" not in html and "조치 기록이 없는 거부 run" not in html
    assert "src=" not in html and "href=" not in html                    # one self-contained document
    assert manager.plugin_view(project) is None                          # the project itself is not a result of the plugin


# --- export formats ------------------------------------------------------------------------------------------------------

def test_a_plugin_adds_an_export_format(work, registry):
    for wrong in ("Mark Down", "1st", "-x", ""):
        with pytest.raises(PluginError, match="Export format name must be lower-case words joined by - or _"):
            register_exporter(wrong)
    for wrong in (("md",), (".",), (". md",)):
        with pytest.raises(PluginError, match="suffixes look like '.md' or '.fhir.json'"):
            register_exporter("markdown", suffixes=wrong)
    with pytest.raises(PluginError, match="goes on the function that writes the file"):
        register_exporter("markdown")("not a function")

    seen = []

    @register_exporter("Lines", suffixes=".lines", description="one line per row")
    def write_lines(dataset, path):
        seen.append(path)
        path.write_text("\n".join(",".join(row) for row in export_frame(dataset).itertuples(index=False)), encoding="utf-8")

    @register_exporter("lab-json", suffixes=(".lab.json",))
    def write_pair(dataset, path):
        side = path.with_name(path.name + ".columns.txt")
        path.write_text("{}", encoding="utf-8")
        side.write_text("\n".join(column.name for column in dataset.columns), encoding="utf-8")
        return [path, side]

    register_exporter("json", suffixes=(".json",))(lambda dataset, path: path.write_text("[]", encoding="utf-8") and None)
    register_exporter("csv", suffixes=(".csv",))(lambda dataset, path: path.write_text("hijacked", encoding="utf-8"))
    dataset = read_tame(work.data)
    assert plugin_export_formats(allow_plugins=True)["lines"] == (".lines",)

    # Registered while the test runs, these count as formats of a plugin from outside: they need the same permission.
    with pytest.raises(ValueError, match="Unsupported export format: lines. Formats: csv, tsv, jsonl, sql, parquet, feather, r_bundle. "
                                         "Formats of plugins outside the trusted folders need --allow-plugins"):
        export_dataset(dataset, work.dir / "out.txt", format="lines")
    with pytest.raises(ValueError, match="Cannot infer export format from output path: .*out.lines. Give --format."):
        export_dataset(dataset, work.dir / "out.lines")

    named = export_dataset(dataset, work.dir / "deep" / "out.txt", format="LINES", allow_plugins=True)      # the folder is made for the plugin
    assert (named.format, named.files) == ("lines", (str(work.dir / "deep" / "out.txt"),))
    assert (work.dir / "deep" / "out.txt").read_text(encoding="utf-8") == "a,1\nb,2\nc,3"
    by_suffix = export_dataset(dataset, work.dir / "out.LINES", allow_plugins=True)
    assert by_suffix.format == "lines" and seen[-1] == work.dir / "out.LINES"
    several = export_dataset(dataset, work.dir / "out.lab.json", allow_plugins=True)                         # the longer suffix, not ".json"
    assert several.format == "lab-json" and [Path(path).name for path in several.files] == ["out.lab.json", "out.lab.json.columns.txt"]
    assert export_dataset(dataset, work.dir / "plain.json", allow_plugins=True).format == "json"

    own = export_dataset(dataset, work.dir / "out.csv", allow_plugins=True)                                 # tametools' own formats win
    assert own.format == "csv" and (work.dir / "out.csv").read_text(encoding="utf-8").startswith("name,value")
    assert export_dataset(dataset, work.dir / "again.txt", format="csv", allow_plugins=True).format == "csv"
    with pytest.raises(ValueError, match="Unsupported export format: yaml. Formats: csv, .* From plugins: .*lines \\(.lines\\)"):
        export_dataset(dataset, work.dir / "out.yaml", format="yaml", allow_plugins=True)


def test_an_export_plugin_that_does_not_write_what_it_says_is_told_so(work, registry):
    register_exporter("silent")(lambda dataset, path: None)
    register_exporter("number")(lambda dataset, path: 3)
    register_exporter("elsewhere")(lambda dataset, path: [path.with_name("not-written.txt")])
    dataset = read_tame(work.data)
    for name, message in (("silent", "Export format silent did not write: .*out.x"), ("number", "Export format number must return the path\\(s\\) it wrote"),
                          ("elsewhere", "Export format elsewhere did not write: .*not-written.txt")):
        with pytest.raises(PluginError, match=message):
            export_dataset(dataset, work.dir / "out.x", format=name, allow_plugins=True)


def test_the_tutorial_export_plugin_writes_markdown_from_the_command_line(work):
    examples = Path(tametools.__file__).resolve().parents[3] / "tutorial" / "plugin_examples"
    written = tametools_cli("export", work.data, "table.md", "--allow-plugins", cwd=work.dir, plugins=examples)
    assert written.code == 0 and "format: markdown" in written.out and "saved: table.md" in written.out
    assert (work.dir / "table.md").read_text(encoding="utf-8") == "| name | value |\n| --- | --- |\n| a | 1 |\n| b | 2 |\n| c | 3 |\n"
    named = tametools_cli("export", work.data, "table.txt", "--format", "markdown", "--allow-plugins", cwd=work.dir, plugins=examples)
    assert named.code == 0 and (work.dir / "table.txt").read_text(encoding="utf-8").startswith("| name | value |")
    listed = tametools_cli("plugins", "--allow-plugins", cwd=work.dir, plugins=examples)
    assert "plugin export formats: markdown (.md)" in listed.out
    refused = tametools_cli("export", work.data, "other.md", cwd=work.dir, plugins=examples)        # the same trust as the plugin itself
    assert refused.code == 1 and "Cannot infer export format from output path: other.md. Give --format." in refused.err
    assert "need --allow-plugins" in refused.err and not (work.dir / "other.md").exists()
    builtin = tametools_cli("export", work.data, "out.csv", cwd=work.dir)                              # unchanged, and no plugin is loaded for it
    assert builtin.code == 0 and "format: csv" in builtin.out

    # The export command of a META pipeline knows the same formats, under the same permission.
    piped = write(work.dir / "piped.tame", DATA.replace("[HELLO]\nN = 2\n", "[PIPELINES]\nDEFAULT = [\"export exports/t.md\", \"export exports/t.txt --format markdown\"]\n"))
    ran = tametools_cli("run-pipeline", piped, "--allow-plugins", cwd=work.dir, plugins=examples)
    assert ran.code == 0 and ran.out.count("format=markdown") == 2
    assert (work.dir / "exports" / "t.md").read_text(encoding="utf-8") == (work.dir / "exports" / "t.txt").read_text(encoding="utf-8") != ""
    denied = tametools_cli("run-pipeline", piped, cwd=work.dir, plugins=examples)
    assert denied.code == 1 and "Cannot infer export format from output path" in denied.err


# --- a sync notices that a plugin outside tametools changed -----------------------------------------------------------

COUNTER = """
    from tametools.plugin_api import register_plugin, table_output

    from _labels import LABEL

    @register_plugin("EXT_COUNT", description="rows per name")
    def ext_count(dataset, meta, step_name, options):
        frame = dataset.df.groupby("name").size().reset_index(name=LABEL)
        return table_output(step_name, frame)
"""
PROJECT = ("<META>\n[SYNC]\nSOURCE = \"source.csv\"\nWORK = \"COUNTS\"\nRESULT = \"result.tame\"\n\n[WORKS]\nCOUNTS = [\"EXT_COUNT\"]\n</META>\n"
           "<DATA>\nname\tvalue\n</DATA>\n")


@pytest.fixture
def external(tmp_path, monkeypatch, registry):
    """A sync project whose WORK runs a plugin kept in a folder outside tametools."""
    plugins = tmp_path / "plugins"
    write(plugins / "counter.py", COUNTER)
    write(plugins / "_labels.py", "LABEL = 'n'\n")
    write(tmp_path / "source.csv", "name,value\na,1\nb,2\na,3\n")
    write(tmp_path / "project.tame", PROJECT)
    monkeypatch.setenv("TAMETOOLS_PLUGIN_PATH", str(plugins))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-user-plugins"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "no-user-plugins"))

    def reload():
        manager._EXTERNAL_LOADED = False            # the folder is read again, as a new process would
    reload()
    yield SimpleNamespace(project=tmp_path / "project.tame", plugins=plugins, reload=reload, result=tmp_path / "result.tame")
    manager._EXTERNAL_LOADED = False
    for name in [name for name in sys.modules if name.startswith(("tametools_dropin_counter", "_labels"))]:
        sys.modules.pop(name, None)


def test_sync_notices_when_a_plugin_outside_tametools_has_changed(external):
    first = sync_project(external.project, allow_plugins=True, now="2026-11-01T09:00:00+09:00")
    assert first.status == "COMMITTED" and list(read_tame(external.result).df["n"]) == ["2", "1"]
    recorded = sync_state(read_tame(external.project))["PLUGIN_SHA256"]
    assert list(recorded) == ["EXT_COUNT"] and len(recorded["EXT_COUNT"]) == 64
    assert manager.plugin_sources(["EXT_COUNT", "VALIDATE", "WESTGARD_QC"], allow_external=True) == recorded   # bundled code is not listed
    assert sync_project(external.project, allow_plugins=True, now="2026-11-01T10:00:00+09:00").status == "UNCHANGED"

    write(external.plugins / "_labels.py", "LABEL = 'count'\n")         # a helper beside the plugin file is part of the plugin
    sys.modules.pop("_labels", None)
    external.reload()
    changed = sync_project(external.project, allow_plugins=True, now="2026-11-02T09:00:00+09:00")
    assert changed.status == "COMMITTED" and changed.tool_changed
    assert "플러그인 구현이 마지막 동기화 이후 바뀌었습니다: EXT_COUNT (분석을 다시 확인합니다)" in changed.reasons
    assert not any("tametools 구현이" in reason for reason in changed.reasons)
    assert list(read_tame(external.result).df["count"]) == ["2", "1"]
    assert sync_state(read_tame(external.project))["PLUGIN_SHA256"]["EXT_COUNT"] != recorded["EXT_COUNT"]
    assert sync_project(external.project, allow_plugins=True, now="2026-11-02T10:00:00+09:00").status == "UNCHANGED"


def test_a_bundled_plugin_imported_a_second_time_is_not_blamed_on_the_next_external_file(external):
    manager.list_plugins({}, allow_external=True)
    trusted = manager._TRUSTED_PLUGINS["QC_ANALYSIS"]
    assert manager.plugin_origin("QC_ANALYSIS") == "bundled" and manager.plugin_origin("EXT_COUNT") == f"folder {external.plugins / 'counter.py'}"
    PLUGIN_REGISTRY["QC_ANALYSIS"] = dataclasses.replace(trusted)      # what `import tametools.plugins.clinical_stats` leaves behind
    errors = list(manager.plugin_load_errors())
    external.reload()
    manager.list_plugins({}, allow_external=True)
    assert PLUGIN_REGISTRY["QC_ANALYSIS"] is trusted                    # put back quietly
    assert manager.plugin_origin("QC_ANALYSIS") == "bundled" and "QC_ANALYSIS" not in manager.plugin_name_conflicts()
    assert manager.plugin_load_errors() == errors


def test_a_project_that_uses_only_bundled_plugins_records_no_plugin_hash(tmp_path):
    project = qc_project(tmp_path)
    assert "PLUGIN_SHA256" not in sync_state(read_tame(project))
    assert sync_project(project, now="2026-10-31T10:00:00+09:00").status == "UNCHANGED"
