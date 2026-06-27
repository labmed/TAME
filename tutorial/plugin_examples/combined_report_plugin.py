from __future__ import annotations

from pathlib import Path
import tempfile

import pandas as pd

from tametools.models import OperationOutput, TameDataset
from tametools.plugin_base import run_plugin
from tametools.plugin_base.base import register_plugin
from tametools.plugin_base.roles import RESULT_ROLE, result_binding
from tametools.reporting import chart_spec, with_visualizations, write_docx_report


@register_plugin(
    "COMBINED_CHEMISTRY_REPORT",
    description="Example report plugin: reuse existing plugins, combine tables and charts, and write a docx report.",
    roles=(RESULT_ROLE,),
)
def combined_chemistry_report_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    bound_results = result_binding(dataset, options)
    warnings = list(bound_results.warnings)

    reference_output = run_plugin(dataset, "REFERENCE_INTERVAL", meta, "REFERENCE_INTERVAL", options)
    summary_options = {**dict(options or {}), "MODE": "RESULT_SUMMARY"}
    summary_output = run_plugin(dataset, "CHEMISTRY_ANALYSIS", meta, "CHEMISTRY_RESULT_SUMMARY", summary_options)
    if reference_output is None or summary_output is None:
        raise ValueError("Required built-in plugins are not available: REFERENCE_INTERVAL, CHEMISTRY_ANALYSIS.")

    warnings.extend(reference_output.warnings or [])
    warnings.extend(summary_output.warnings or [])
    summary_table = summary_output.table if summary_output.table is not None else pd.DataFrame()

    chart = chart_spec(
        "SUMMARY_MEDIAN",
        type="bar",
        title="검사항목별 중앙값",
        x="검사항목명",
        y="중앙값",
        table="summary",
    )

    report_path = Path(str(options.get("REPORT_PATH") or Path(tempfile.gettempdir()) / "combined_chemistry_report.docx"))
    report_output = OperationOutput(
        name=step_name,
        table=summary_table,
        tables={
            "reference_interval": reference_output.table if reference_output.table is not None else pd.DataFrame(),
            "summary": summary_table,
        },
        charts=[chart],
        warnings=warnings,
        message="Combined chemistry report tables and chart.",
    )
    write_docx_report(
        report_path,
        title="임상화학 요약 보고서",
        outputs=[report_output],
        summary="REFERENCE_INTERVAL과 CHEMISTRY_ANALYSIS를 재사용해 생성한 예제 보고서입니다.",
    )

    result_dataset = summary_output.dataset or dataset
    result_dataset = with_visualizations(result_dataset, [chart])
    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=summary_table,
        tables=report_output.tables,
        charts=[chart],
        files=[str(report_path)],
        warnings=warnings,
        message=f"combined_chemistry_report rows={len(summary_table)} report={report_path}",
    )
