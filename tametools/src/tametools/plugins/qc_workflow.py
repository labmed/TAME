"""A small QC workflow assembled from shared preprocessing, plugins and reports."""
from tametools.plugin_api import PluginWorkflow, ci_get, register_plugin


@register_plugin("QC_WORKFLOW", description="QC workflow: preparation, precision, rules, trend, run review and shared reports.", reporting=True,
    dependencies=("QC_ANALYSIS", "RESULT_TREND", "QC_RUN_REVIEW"))
def qc_workflow(dataset, meta, step_name, options):
    cfg = {str(k).upper(): v for k, v in options.items()}
    allowed = {"PREPROCESS", "TREND_WINDOW", "OUTPUT_DIR", "TITLE", "REPORT", "REPORT_MAX_ROWS"}
    if set(cfg) - allowed:
        raise ValueError("Unknown QC_WORKFLOW option: " + ", ".join(sorted(set(cfg)-allowed)))
    flow = PluginWorkflow(dataset)
    flow.validate()
    source = "validate"
    if ci_get(cfg, "PREPROCESS"):
        flow.action("prepare", cfg["PREPROCESS"], source=source)
        source = "prepare"
    flow.plugin("precision", "QC_ANALYSIS", source=source, options={"MODE": "PRECISION"})
    flow.plugin("rules", "QC_ANALYSIS", source=source, options={"MODE": "WESTGARD"})
    flow.plugin("trend", "RESULT_TREND", source="rules", options={
        "RESULT_IDS": ["z"], "DATE": "id:time", "PERIOD": "D", "WINDOW": cfg.get("TREND_WINDOW", 3),
        "GROUP_BY_IDS": ["result_id", "level", "lot", "instrument", "baseline_key", "baseline_valid_from", "baseline_valid_to"]})
    flow.plugin("review", "QC_RUN_REVIEW", source="rules")
    return flow.finish(step_name, primary="review", title=cfg.get("TITLE", "QC 업무 보고서"),
        summary="관리물질 자료를 기본 기능으로 준비하고, 정밀도·관리규칙·추세 분석의 결과를 연결하여 run별 검토 항목을 정리했습니다.",
        output_dir=cfg.get("OUTPUT_DIR"), max_table_rows=cfg.get("REPORT_MAX_ROWS", 40),
        report_options={str(k).lower():v for k,v in cfg.get("REPORT", {}).items()})
