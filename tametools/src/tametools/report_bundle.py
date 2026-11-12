"""Shared HTML, Word, CSV and TAME export for composed plugin outputs."""
from __future__ import annotations

import base64
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re
import zipfile

import pandas as pd

from .io import write_tame
from .models import OperationOutput
from .reporting import write_docx_report


def write_report_bundle(directory, *, title, output, source, stages, max_table_rows=40,
                        tables=None, table_columns=None, table_titles=None):
    """Write full machine-readable outputs and bounded human-readable previews.

    Destination must be new. Table selections affect only the human report;
    CSV and per-step TAME files preserve complete results and processing history.
    """
    directory = Path(directory)
    if isinstance(max_table_rows, str) and re.fullmatch(r"[1-9][0-9]*", max_table_rows.strip()):
        max_table_rows = int(max_table_rows.strip())
    if isinstance(max_table_rows, bool) or not isinstance(max_table_rows, (int, float)) or int(max_table_rows) != max_table_rows or max_table_rows < 1:
        raise ValueError("Report row limit must be a positive integer")
    max_table_rows = int(max_table_rows)
    chosen = list(tables) if tables is not None else list(output.tables or {})
    if set(chosen) - set(output.tables or {}):
        raise ValueError("Unknown report table")
    table_columns, table_titles = table_columns or {}, table_titles or {}
    display = {}
    for name in chosen:
        frame = output.tables[name]
        columns = table_columns.get(name, list(frame.columns))
        if set(columns) - set(frame.columns):
            raise ValueError("Unknown report column in " + name)
        label = table_titles.get(name, name)
        if label in display:
            raise ValueError("Duplicate report table title")
        display[label] = frame.loc[:, columns]
    for name in output.tables or {}:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name):
            raise ValueError("Report table name is not a safe filename")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "tables").mkdir()
    (directory / "results").mkdir()
    for name, frame in (output.tables or {}).items():
        frame.to_csv(directory / "tables" / (name + ".csv"), index=False, encoding="utf-8-sig")
    write_tame(directory / "analysis_input.tame", source, standardize_sex_values=False)
    for name, stage in stages.items():
        write_tame(directory / "results" / (name + ".tame"), stage.dataset, standardize_sex_values=False)
    write_tame(directory / "result.tame", output.dataset, standardize_sex_values=False)
    workflow = deepcopy(output.dataset.meta.get("WORKFLOW_RESULT", {}))
    (directory / "workflow.json").write_text(json.dumps(workflow, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    steps = pd.DataFrame([{"단계": step["name"], "입력": step["source"], "기능": step["operation"],
        "결과 행": len(stages[step["name"]].dataset.df)} for step in workflow.get("STEPS", [])])
    report = OperationOutput(name=title, tables={"실행 흐름": steps, **display}, charts=output.charts,
                             warnings=output.warnings, message=output.message)
    # Charts already contain bounded, explicitly bound rows. They do not rely
    # on the display columns or translated table titles above.
    write_docx_report(directory / "report.docx", title=title, outputs=[report],
                      max_table_rows=max_table_rows)
    html = ["<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>" + escape(title) + "</title><style>body{font:15px/1.65 system-ui,sans-serif;color:#22334a;max-width:1200px;margin:32px auto;padding:0 20px}h1,h2{color:#145e68}section{margin:28px 0;overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d9e2eb;padding:7px;text-align:left}th{background:#edf4f6}img{max-width:100%}.note{background:#f2f6fa;padding:12px}details{margin:10px 0}pre{white-space:pre-wrap}@media print{body{margin:0}thead{display:table-header-group}}</style></head><body>",
        "<h1>" + escape(title) + "</h1><p>" + escape(output.message or "") + "</p>"]
    for warning in output.warnings or []:
        html.append("<p class='note'>" + escape(str(warning)) + "</p>")
    for name, frame in {"실행 흐름": steps, **display}.items():
        html.append("<section><h2>"+escape(name)+"</h2>"+frame.head(max_table_rows).to_html(index=False,escape=True,na_rep="—",float_format=lambda v:f"{v:.4g}")+
                    f"<p>전체 {len(frame)}행 · 문서 표시 {min(len(frame), max_table_rows)}행</p></section>")
    for index, chart in enumerate(output.charts or [], 1):
        png = directory / "report_charts" / f"chart_{index:02d}.png"
        title_text = str(chart.get("TITLE", chart.get("name", "Chart")))
        html.append("<section><h2>"+escape(title_text)+"</h2><img alt='"+escape(title_text,quote=True)+"' src='data:image/png;base64,"+base64.b64encode(png.read_bytes()).decode()+"'></section>")
    html.append("<details><summary>적용된 단계와 설정</summary><pre>"+escape(json.dumps(workflow,ensure_ascii=False,indent=2,default=str))+"</pre></details></body></html>")
    (directory / "report.html").write_text("\n".join(html), encoding="utf-8")
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    archive = directory / "report.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(directory).as_posix())
    return [*files, archive]
