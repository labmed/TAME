"""Small task-specific interpretation of an existing QC plugin's result."""
from tametools.plugin_api import ci_get, register_plugin, require_result, table_output

# The columns of QC_ANALYSIS (MODE = WESTGARD) this plugin reads. Asked for by name, so that a result that
# does not have them is refused in words instead of failing inside the grouping below.
NEEDED = ("result_id", "instrument", "lot", "run", "time", "violation", "status")


@register_plugin("QC_RUN_REVIEW", description="Summarize existing QC rule flags per analyte and run; manual review only.",
                 dependencies=("QC_ANALYSIS",))
def qc_run_review(dataset, meta, step_name, options):
    config = ci_get(dataset.meta, "PLUGIN", {})
    if ci_get(config, "NAME") != "QC_ANALYSIS" or ci_get(config, "MODE") != "WESTGARD":
        raise ValueError("QC_RUN_REVIEW requires QC_ANALYSIS MODE=WESTGARD output")
    require_result(dataset, "QC_ANALYSIS", columns=NEEDED)
    frame = dataset.df.copy()
    frame["flagged"] = ~frame.violation.isin(["NONE", "NE"])
    frame["not_evaluated"] = frame.status.ne("evaluated")
    keys = ["result_id", "instrument", "lot", "run", "time"]
    summary = frame.groupby(keys, sort=True, dropna=False).agg(
        observations=("status", "size"), flagged=("flagged", "sum"),
        not_evaluated=("not_evaluated", "sum"),
        rules=("violation", lambda values: ", ".join(sorted(set(v for value in values for v in value.split(", ") if v not in {"NONE", "NE"}))))).reset_index()
    summary["review"] = "NO_RULE_FLAG"
    summary.loc[(summary.flagged > 0) | (summary.not_evaluated > 0), "review"] = "REVIEW"
    return table_output(step_name, summary,
        message="Run review combines existing rule flags and unevaluated measurements.",
        warnings=["NO_RULE_FLAG means no implemented rule was triggered; it is not an automatic result-release decision."])
