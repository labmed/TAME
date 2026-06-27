"""독립 검증용 보고서 플러그인 (회귀 테스트 자산).

발주사 측이 PLUGIN_AUTHOR_GUIDE 문서만 보고 작성한 제3자 검증 예제다. 수행사 예제
(`combined_report_plugin.COMBINED_CHEMISTRY_REPORT`, RI+CHEMISTRY)와 의도적으로 다른 조합을
써서, 플러그인 확장 계약이 데모용 특수처리가 아니라 범용으로 동작하는지 고정한다.

수행사 예제와 다른 점:
- 재사용 플러그인 3종: QC_ANALYSIS(PRECISION) + ABNORMAL_FLAG(RATE) + RESULT_TREND(D)
- 차트 타입 혼합: BAR 2개 + LINE 1개 (BAR 일변도가 아님)
- role helper(result_binding / RESULT_ROLE) 사용
- 컬럼명이 전부 비표준인 입력에서 태그만으로 동작 (샘플 tame 참고)
- 산출물: 다중 표 + charts + files(docx)

오직 공개 API(PLUGIN_AUTHOR_GUIDE 기재)만 사용한다.
"""
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
    "INDEP_LAB_REPORT",
    description="독립 검증: QC + 이상률 + 추세를 재사용해 차트 포함 docx 보고서를 만든다.",
    roles=(RESULT_ROLE,),
)
def indep_lab_report_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    bound = result_binding(dataset, options)          # role helper (컬럼명 비의존)
    warnings = list(bound.warnings)

    qc = run_plugin(dataset, "QC_ANALYSIS", meta, "QC", {"MODE": "PRECISION"})
    rate = run_plugin(dataset, "ABNORMAL_FLAG", meta, "RATE", {"MODE": "RATE"})
    trend = run_plugin(dataset, "RESULT_TREND", meta, "TREND", {"PERIOD": "D"})
    if qc is None or rate is None or trend is None:
        raise ValueError("필수 내장 플러그인을 찾을 수 없습니다: QC_ANALYSIS, ABNORMAL_FLAG, RESULT_TREND")
    for out in (qc, rate, trend):
        warnings.extend(out.warnings or [])

    def frame(out) -> pd.DataFrame:
        return out.table if out.table is not None else pd.DataFrame()

    tables = {
        "qc_precision": frame(qc),
        "abnormal_rate": frame(rate),
        "result_trend": frame(trend),
    }

    charts = [
        chart_spec("QC_CV", type="bar", title="검사항목별 CV%", x="level", y="cv_percent", table="qc_precision"),
        chart_spec("ABNORMAL_RATE", type="bar", title="검사항목별 이상결과율(%)", x="검사명", y="abnormal_rate", table="abnormal_rate"),
        chart_spec("TREND_MA", type="line", title="일별 이동평균 추세", x="period", y="moving_avg", series="test", table="result_trend"),
    ]

    report_path = Path(str(options.get("REPORT_PATH") or Path(tempfile.gettempdir()) / "indep_lab_report.docx"))
    report = OperationOutput(name=step_name, table=tables["qc_precision"], tables=tables, charts=charts)
    write_docx_report(
        report_path,
        title="독립 검증 임상검사 보고서",
        outputs=[report],
        summary="QC_ANALYSIS, ABNORMAL_FLAG, RESULT_TREND를 재사용해 만든 독립 검증 보고서입니다.",
    )

    result_dataset = with_visualizations(qc.dataset or dataset, charts)
    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=tables["qc_precision"],
        tables=tables,
        charts=charts,
        files=[str(report_path)],
        warnings=warnings,
        message=f"indep_lab_report charts={len(charts)} report={report_path}",
    )
