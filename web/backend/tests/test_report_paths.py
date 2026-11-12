"""Where reports are written is the workbench's decision: an opened file does not choose a path on this machine."""
from pathlib import Path

import pytest

from tametools.io import read_tame
from app.tametools_bridge import dataset_payload, run_meta_analysis_payload, run_web_plugin_payload
from app.workbench_analysis import reference_analysis, reference_plugin

ROOT = Path(__file__).resolve().parents[3]
OPTIONS = {"RESULT_IDS": ["alt"], "OUTLIER_METHOD": "NONE"}


@pytest.fixture
def kenya():
    return dataset_payload(read_tame(ROOT / "web/backend/examples/kenya_reference.tame"), filename="kenya.tame")


def with_setting(payload, section, line):
    marker = f"[{section}]"
    text = payload["metaText"]
    assert marker in text
    return {**payload, "metaText": text.replace(marker, marker + "\n" + line, 1)}


@pytest.mark.parametrize("key", ["REPORT_PATH", "report_path", "OUTPUT_DIR"])
def test_a_report_path_carried_by_the_file_is_not_followed(kenya, tmp_path, key):
    outside = tmp_path / "chosen_by_the_file" / ("report.docx" if key.upper() == "REPORT_PATH" else "folder")
    workspace = tmp_path / "workspace"
    payload = with_setting(kenya, reference_plugin(), f'{key} = "{outside.as_posix()}"')
    result = reference_analysis(payload, dict(OPTIONS), workspace)
    assert not outside.exists() and not outside.parent.exists()
    files = result["reportFiles"]
    assert [item["filename"] for item in files] == ["reference_interval_report_ko.docx", "reference_interval_report.zip"]
    folder = workspace / "analysis_reports" / files[0]["url"].split("/")[-2]
    assert all((folder / item["filename"]).is_file() for item in files)             # what is offered is there
    assert "REPORT_PATH" not in result["analysisSettings"] and "OUTPUT_DIR" not in result["analysisSettings"]


def test_without_a_workspace_nothing_is_written_anywhere(kenya, tmp_path):
    outside = tmp_path / "chosen_by_the_file" / "report.docx"
    payload = with_setting(kenya, reference_plugin(), f'REPORT_PATH = "{outside.as_posix()}"')
    result = reference_analysis(payload, dict(OPTIONS))
    assert not outside.parent.exists() and "reportFiles" not in result


def test_a_path_typed_into_the_request_is_refused(kenya, tmp_path):
    for key in ("REPORT_PATH", "OUTPUT_DIR"):
        with pytest.raises(ValueError, match="OUTPUT_DIR/REPORT_PATH"):
            reference_analysis(kenya, {**OPTIONS, key: str(tmp_path / "x.docx")}, tmp_path / "workspace")
    with pytest.raises(ValueError, match="REPORT_PATH"):
        run_web_plugin_payload(kenya, {"plugin": "LAB_TAT_ANALYSIS", "pluginOptions": {"REPORT_PATH": str(tmp_path / "x.docx")}}, tmp_path)


def test_a_stored_analysis_cannot_name_a_path_either(kenya, tmp_path):
    outside = tmp_path / "chosen_by_the_file" / "report.docx"
    extra = f'\n[ANALYSES.OLD_RI]\nPLUGIN = "REFERENCE_INTERVAL"\nREPORT_PATH = "{outside.as_posix()}"\n'
    result = run_meta_analysis_payload({**kenya, "metaText": kenya["metaText"] + extra}, {"analysis": "OLD_RI"}, tmp_path / "workspace")
    assert result["rows"] and not outside.parent.exists()


def analysis_source(tmp_path, settings):
    """120 values, one of them far out: whether it is removed shows which settings were applied."""
    rows = "\n".join(f"s{index}\t{value}" for index, value in enumerate([*range(1, 120), 400], 1))
    path = tmp_path / "source.tame"
    path.write_text('<META>\n[COLUMN.subject]\nID = "subject"\nTAGS = ["ID(subject)", "STR"]\n\n[COLUMN.alt]\nID = "alt"\nUNIT = "U/L"\n'
                    'TAGS = ["RESULT", "NUM"]\n\n' + settings + "</META>\n<DATA>\nsubject\talt\n" + rows + "\n</DATA>\n", encoding="utf-8")
    return dataset_payload(read_tame(path), filename=path.name)


@pytest.mark.parametrize("section, keys", [
    ("REFERENCE_INTERVAL_EP28", ("OUTLIER_METHOD", "OUTLIER_ACTION", "REPORT_PATH")),     # the plugin's other name
    ("ri_ep28", ("outlier_method", "outlier_action", "report_path")),                     # another letter case
    ("RI_EP28", ("OUTLIER_METHOD", "OUTLIER_ACTION", "REPORT_PATH")),
])
def test_the_saved_analysis_input_runs_again_to_the_same_result(tmp_path, section, keys):
    from tametools.plugin_base import run_plugin

    outside = tmp_path / "chosen_by_the_file" / "report.docx"
    payload = analysis_source(tmp_path, f'[{section}]\n{keys[0]} = "TUKEY"\n{keys[1]} = "REMOVE"\n{keys[2]} = "{outside.as_posix()}"\n')
    result = reference_analysis(payload, {"RESULT_IDS": ["alt"], "OUTLIER_METHOD": "NONE", "OUTLIER_ACTION": "REMOVE", "CI_METHOD": "NONE"},
                                tmp_path / "workspace")
    shown = next(row for row in result["analysisTables"]["reference_intervals"] if row["primary"])
    assert shown["n"] == 120                                           # the request said NONE: nothing was removed

    folder = tmp_path / "workspace" / "analysis_reports" / result["reportFiles"][0]["url"].split("/")[-2]
    saved = read_tame(folder / "analysis_input.tame")
    sections = [key for key in saved.meta if str(key).upper() in {"RI_EP28", "REFERENCE_INTERVAL_EP28"}]
    assert sections == ["RI_EP28"]                                     # one place for the settings, under the plugin's name
    assert saved.meta["RI_EP28"]["OUTLIER_METHOD"] == "NONE"
    assert not {"REPORT_PATH", "OUTPUT_DIR"} & {str(key).upper() for key in saved.meta["RI_EP28"]}

    again = run_plugin(saved, "RI_EP28", saved.meta, "RI_EP28", {})
    row = again.table.loc[again.table["primary"].astype(bool)].iloc[0]
    assert (int(row["n"]), row["ref_low"], row["ref_high"]) == (shown["n"], shown["ref_low"], shown["ref_high"])
    assert not outside.parent.exists()


def test_a_workbook_can_be_renamed_right_after_it_was_looked_at(tmp_path):
    import openpyxl
    from app.tametools_bridge import convert_xlsx_selection, inspect_xlsx, is_tame_workbook
    from tametools.io import read_xlsx

    for number, look in enumerate((inspect_xlsx, is_tame_workbook, read_xlsx,
                                   lambda path: convert_xlsx_selection(path, sheet_names=["DATA"], mode="single"))):
        path = tmp_path / f"book{number}.xlsx"
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "DATA"
        for row in (["id", "value"], *[[f"r{index}", index] for index in range(50)]):
            sheet.append(row)
        workbook.save(path)
        workbook.close()
        kept = look(path)                                              # still referenced, as while a request is answered
        path.rename(path.with_name(f"renamed{number}.xlsx"))           # fails on Windows while a handle is left on the file
        assert kept is not None
