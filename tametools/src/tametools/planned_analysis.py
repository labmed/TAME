"""Declarative quantitative exploration with separately labelled survey estimates."""
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import sys

import numpy as np
import pandas as pd

from .analysis_contract import check_contract_version, column_ids, measurement_values, resolve_id, strict_numbers
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import ColumnSpec, OperationOutput, ValidationIssue
from .reporting import chart_spec
from .measurement_tags import is_categorical_measurement
from .tags import TAG_TOKEN_RE, normalize_tag


def _declared_plan(dataset):
    keys = [key for key in dataset.meta if str(key).upper() in {"ANALYSIS_PLAN", "CLINICAL_ANALYSIS"}]
    if len(keys) > 1:
        raise ValueError("Declare only ANALYSIS_PLAN; do not combine current and legacy plan tables")
    return dataset.meta[keys[0]] if keys else None


def _table(value, required, optional=(), label="analysis table"):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError(f"{label}: required {sorted(required)}, optional {sorted(optional)}")
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
        raise ValueError("Analysis group/pair/contrast IDs must be simple identifiers of at most 64 characters")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Analysis cut points and numeric codes must be finite TOML numbers")
    return float(value)


def _conditions(dataset, conditions):
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("ALL_OF must be a nonempty list of conditions")
    mask = np.ones(len(dataset.df), dtype=bool)
    missing = np.zeros(len(dataset.df), dtype=bool)
    for condition in conditions:
        _table(condition, {"COLUMN_ID"}, {"MIN", "MAX_EXCLUSIVE", "IN_NUMBERS", "IN_TEXT"}, "condition")
        column = resolve_id(dataset, condition["COLUMN_ID"])
        keys = condition.keys() - {"COLUMN_ID"}
        if not keys or (keys & {"IN_NUMBERS", "IN_TEXT"} and len(keys) != 1):
            raise ValueError("Use numeric bounds, IN_NUMBERS, or IN_TEXT in each condition")
        raw = dataset.df[column.name]
        absent = raw.map(lambda v: cell_state(v) != STATE_VALUE).to_numpy()
        missing |= absent
        if "IN_TEXT" in keys:
            choices = condition["IN_TEXT"]
            if not isinstance(choices, list) or not choices or any(not isinstance(v, str) or not v.strip() for v in choices):
                raise ValueError("IN_TEXT requires nonempty exact strings")
            selected = raw.isin(choices).to_numpy()
        else:
            values = strict_numbers(raw, "Group " + condition["COLUMN_ID"])
            if "IN_NUMBERS" in keys:
                choices = condition["IN_NUMBERS"]
                if not isinstance(choices, list) or not choices:
                    raise ValueError("IN_NUMBERS requires a nonempty number list")
                selected = values.isin([_number(v) for v in choices]).to_numpy()
            else:
                lower = _number(condition["MIN"]) if "MIN" in keys else -np.inf
                upper = _number(condition["MAX_EXCLUSIVE"]) if "MAX_EXCLUSIVE" in keys else np.inf
                if lower >= upper:
                    raise ValueError("MIN must be less than MAX_EXCLUSIVE")
                top_code = ci_get(dataset.column_metadata(column), "TOP_CODE_VALUE", None)
                if top_code is not None:
                    top_code = _number(top_code)
                    if lower > top_code or ("MAX_EXCLUSIVE" in keys and upper > top_code):
                        raise ValueError("Group boundaries cannot resolve ages beyond the released top code")
                selected = (values.ge(lower) & values.lt(upper)).to_numpy()
        mask &= selected & ~absent
    return mask, missing


def _eligibility(dataset, column):
    from .observation_contract import result_status
    accepted = result_status(dataset, column)[0].to_numpy()
    config = dataset.column_metadata(column)
    identifier, rule = ci_get(config, "ELIGIBLE_ID", None), ci_get(config, "ELIGIBILITY", None)
    if identifier is not None and rule is not None:
        raise ValueError("Declare ELIGIBLE_ID or ELIGIBILITY, not both")
    if rule is not None:
        _table(rule, {"ALL_OF"}, label="ELIGIBILITY")
        eligible, missing = _conditions(dataset, rule["ALL_OF"])
        return eligible & accepted, missing
    if identifier is not None:
        values = strict_numbers(dataset.df[resolve_id(dataset, identifier).name], "Eligibility")
        if values.isna().any() or not values.isin([0, 1]).all():
            raise ValueError("Eligibility indicators must be nonmissing 0/1")
        return values.eq(1).to_numpy() & accepted, np.zeros(len(values), dtype=bool)
    return accepted, np.zeros(len(dataset.df), dtype=bool)


def _plan(dataset, options):
    check_contract_version(dataset)
    from .observation_contract import observation_info
    from .measurement_tags import require_measurement_tags
    observation_info(dataset)
    require_measurement_tags(dataset)
    identifiers = column_ids(dataset)
    _table(options, {"VERSION", "MODE", "POLICIES", "PRIMARY_POLICY"},
           {"RESULT_IDS", "RESULT_TAGS", "GROUPS", "PAIRS", "CONTRASTS", "MODELS", "MISSINGNESS", "PLOT_GROUP", "PLOT_RESULT_IDS", "HISTOGRAM_BINS", "HISTOGRAM_SCALES"}, "ANALYSIS_PLAN")
    if ("RESULT_IDS" in options) == ("RESULT_TAGS" in options):
        raise ValueError("Declare exactly one of RESULT_IDS or RESULT_TAGS")
    if type(options["VERSION"]) is not int or options["VERSION"] != 1:
        raise ValueError("ANALYSIS_PLAN.VERSION must be 1")
    if options["MODE"] not in {"SURVEY", "SAMPLE"}:
        raise ValueError("MODE must explicitly be SURVEY or SAMPLE")
    has_design = ci_get(dataset.meta, "SURVEY", None) is not None
    if has_design != (options["MODE"] == "SURVEY"):
        raise ValueError("MODE must match the presence of SURVEY; do not silently discard a survey design")
    for key in ("RESULT_IDS" if "RESULT_IDS" in options else "RESULT_TAGS", "POLICIES"):
        values = options[key]
        if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values):
            raise ValueError(key + " must be a nonempty list of distinct strings")
    result_ids = options.get("RESULT_IDS")
    if "RESULT_TAGS" in options:
        tags = options["RESULT_TAGS"]
        if any(not TAG_TOKEN_RE.fullmatch(tag) for tag in tags) or len({normalize_tag(t) for t in tags}) != len(tags):
            raise ValueError("RESULT_TAGS must contain valid, normalized-distinct tag tokens")
        selected = dataset.find_columns(required_tags=tags)
        if not selected:
            raise ValueError("RESULT_TAGS matched no columns")
        result_ids = []
        for column in selected:
            identifier = ci_get(dataset.column_metadata(column), "ID", None)
            if identifier not in identifiers:
                raise ValueError("Every RESULT_TAGS match requires a stable COLUMN.ID")
            result_ids.append(identifier)
        result_ids.sort()
    if not set(options["POLICIES"]) <= {"RELEASED", "VALUE", "DELETE", "MIDPOINT"} or options["PRIMARY_POLICY"] not in options["POLICIES"]:
        raise ValueError("Select RELEASED, VALUE or DELETE policies and a primary policy among them")
    groups = {"ALL": (np.ones(len(dataset.df), dtype=bool), np.zeros(len(dataset.df), dtype=bool))}
    definitions = options.get("GROUPS", {})
    if not isinstance(definitions, dict):
        raise ValueError("GROUPS must be a table")
    for name, rule in definitions.items():
        _id(name)
        if name == "ALL":
            raise ValueError("ALL is a reserved group")
        _table(rule, {"ALL_OF"}, label="GROUPS." + name)
        groups[name] = _conditions(dataset, rule["ALL_OF"])
    columns, eligibility = {}, {}
    for identifier in result_ids:
        column = resolve_id(dataset, identifier)
        if is_categorical_measurement(dataset, column):
            raise ValueError("NOMINAL/ORDINAL results cannot be selected for quantitative analysis")
        if not dataset.column_has_tag(column, "RESULT") or not dataset.column_has_any_tag(column, ["NUM", "<NUM>"]) or dataset.column_has_tag(column, "AGE"):
            raise ValueError("Analysis results require RESULT and NUM/<NUM>; age is not a chemistry result")
        unit = ci_get(dataset.column_metadata(column), "UNIT", None)
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(identifier + " requires a declared UNIT")
        from .measurement_tags import require_quantitative
        require_quantitative(dataset, column, require_unit=True)
        columns[identifier] = column
        eligibility[identifier] = _eligibility(dataset, column)
    for key, required in [("PAIRS", {"ID", "X_ID", "Y_ID"}), ("CONTRASTS", {"ID", "GROUP_A", "GROUP_B"})]:
        entries = options.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(key + " must be an array of tables")
        used = set()
        for entry in entries:
            _table(entry, required, label=key)
            name = _id(entry["ID"])
            if name in used:
                raise ValueError("Duplicate " + key + " ID")
            used.add(name)
            a, b = (entry["X_ID"], entry["Y_ID"]) if key == "PAIRS" else (entry["GROUP_A"], entry["GROUP_B"])
            choices = columns if key == "PAIRS" else groups
            if not isinstance(a, str) or not isinstance(b, str) or a not in choices or b not in choices or a == b:
                raise ValueError(key + " requires two different, declared results/groups")
    if options.get("CONTRASTS") and options["MODE"] != "SURVEY":
        raise ValueError("CONTRASTS requires SURVEY; SAMPLE summaries do not imply population inference")
    if options.get("PLOT_GROUP", "ALL") not in groups:
        raise ValueError("Unknown PLOT_GROUP")
    plots = options.get("PLOT_RESULT_IDS", list(columns))
    if not isinstance(plots, list) or any(not isinstance(v, str) for v in plots) or len(set(plots)) != len(plots) or not set(plots) <= columns.keys():
        raise ValueError("PLOT_RESULT_IDS must be a distinct subset of RESULT_IDS")
    bins = options.get("HISTOGRAM_BINS", 20)
    if type(bins) is not int or not 5 <= bins <= 100:
        raise ValueError("HISTOGRAM_BINS must be an integer from 5 to 100")
    scales = options.get("HISTOGRAM_SCALES", {})
    if not isinstance(scales, dict) or not scales.keys() <= columns.keys() or any(scale not in {"linear", "log10"} for scale in scales.values()):
        raise ValueError("HISTOGRAM_SCALES maps declared results to linear or log10")
    if len(dataset.df.columns) != len(set(dataset.df.columns)):
        raise ValueError("Duplicate source column names")
    if any(str(v).startswith(("__analysis_plan_", "__clinical_")) for v in [*identifiers, *dataset.df.columns]):
        raise ValueError("__analysis_plan_ is reserved for internal analysis bindings")
    from .analysis_models import validate_extensions
    validate_extensions(dataset, options, columns, groups)
    return columns, eligibility, groups


def analysis_plan_validation_issues(dataset):
    try:
        config = _declared_plan(dataset)
        if config is None:
            return []
        _plan(dataset, config)
    except (ValueError, TypeError, KeyError) as exc:
        return [ValidationIssue(0, "META", "ANALYSIS_PLAN", None, str(exc))]
    return []


def _values(dataset, column, policy):
    if ci_get(dataset.column_metadata(column), "CENSORING_INTERVAL", None) is not None:
        values, censored = measurement_values(dataset, column, policy)
        return values.to_numpy(), censored.to_numpy(), "row_interval_binding"
    if ci_get(dataset.column_metadata(column), "CENSORING", None) is not None:
        values, censored = measurement_values(dataset, column, policy)
        return values.to_numpy(), censored.to_numpy(), "source_flag"
    if policy == "MIDPOINT":
        raise ValueError("MIDPOINT requires an explicit interval-censoring binding")
    from .analysis import parse_comparator_number
    values, flags = [], []
    for value in dataset.df[column.name]:
        if cell_state(value) != STATE_VALUE:
            values.append(np.nan)
            flags.append(False)
            continue
        parsed = parse_comparator_number(str(value).strip())
        if parsed is None or not math.isfinite(parsed[1]):
            raise ValueError(column.name + ": invalid finite numeric result")
        operator, number = parsed
        censored = operator not in {"", "="}
        if censored and not dataset.column_has_tag(column, "<NUM>"):
            raise ValueError("Comparator results require <NUM>")
        if censored and policy == "RELEASED":
            raise ValueError("RELEASED needs a source binding for censored results; explicitly choose DELETE or VALUE")
        flags.append(censored)
        values.append(np.nan if censored and policy == "DELETE" else number)
    return np.asarray(values, dtype=float), np.asarray(flags), "explicit_comparator" if any(flags) else "not_reported"


def _sample_summary(values):
    row = dict(mean=np.nan, sd=np.nan, median=np.nan, q025=np.nan, q25=np.nan, q75=np.nan, q975=np.nan,
               minimum=np.nan, maximum=np.nan, cv_percent=np.nan, geometric_mean=np.nan, geometric_sd=np.nan,
               geometric_status="empty")
    if not len(values):
        return row
    q = np.quantile(values, [.025, .25, .5, .75, .975], method="linear")
    mean = float(np.mean(values))
    sd = float(np.std(values, ddof=1)) if len(values) > 1 else np.nan
    row.update(mean=mean, sd=sd, q025=q[0], q25=q[1], median=q[2], q75=q[3], q975=q[4],
               minimum=float(np.min(values)), maximum=float(np.max(values)),
               cv_percent=100*sd/mean if mean > 0 else np.nan,
               geometric_status="nonpositive_values" if np.any(values <= 0) else "ok")
    if np.all(values > 0):
        logs = np.log(values)
        row.update(geometric_mean=float(np.exp(logs.mean())),
                   geometric_sd=float(np.exp(logs.std(ddof=1))) if len(values) > 1 else np.nan)
    return row


def _survey(dataset, columns, eligibility, groups, values, policies):
    from .survey import survey_describe
    work = dataset.replace(df=dataset.df.copy(), columns=list(dataset.columns), meta=deepcopy(dataset.meta))
    metadata = work.meta.setdefault("COLUMN", {})
    def add(identifier, array, extra=None):
        work.df[identifier] = array
        work.columns.append(ColumnSpec(identifier, identifier, ["NUM", "RESULT", "NULLABLE"]))
        metadata[identifier] = {"ID": identifier, **(extra or {})}
    domain_ids, group_lookup = [], {"ALL": "ALL"}
    for i, (name, (mask, _)) in enumerate(groups.items()):
        if name != "ALL":
            identifier = f"__analysis_plan_group_{i}"
            add(identifier, mask.astype(int))
            domain_ids.append(identifier)
            group_lookup[identifier] = name
    temporary = {}
    for i, (identifier, column) in enumerate(columns.items()):
        eligible_id, value_id = f"__analysis_plan_eligible_{i}", f"__analysis_plan_value_{i}"
        add(eligible_id, eligibility[identifier][0].astype(int))
        add(value_id, np.zeros(len(work.df)), {"UNIT": ci_get(dataset.column_metadata(column), "UNIT"), "ELIGIBLE_ID": eligible_id})
        temporary[value_id] = identifier
    results = []
    for policy in policies:
        for temp, identifier in temporary.items():
            work.df[temp] = values[identifier, policy][0]
        result = survey_describe(work, {"RESULT_IDS": list(temporary), "DOMAIN_IDS": domain_ids, "POLICIES": ["RELEASED"]}).table
        for row in result.to_dict("records"):
            row.update(result_id=temporary[row["result_id"]], group=group_lookup[row["domain"]], policy=policy)
            results.append({key: row[key] for key in ["result_id", "group", "policy", "unit", "design_n", "zero_weight_n",
                "design_strata", "design_psus", "domain_n", "analysis_n", "weight_sum", "df", "mean", "se", "ci95_low", "ci95_high", "status", "engine"]})
    return pd.DataFrame(results)


def _contrasts(dataset, options, columns, eligibility, groups, values, survey):
    from samplics.estimation import TaylorEstimator
    from samplics.utils.types import PopParam
    from scipy.stats import t
    design = ci_get(dataset.meta, "SURVEY")
    weights = strict_numbers(dataset.df[resolve_id(dataset, ci_get(design, "WEIGHT_ID")).name], "Weight").to_numpy()
    positive = weights > 0
    labels = dataset.df.loc[positive, [resolve_id(dataset, ci_get(design, key)).name for key in ["STRATUM_ID", "PSU_ID"]]].astype(str)
    strata = pd.factorize(labels.iloc[:, 0], sort=True)[0]
    psus = pd.factorize(pd.MultiIndex.from_frame(labels), sort=True)[0]
    weights = weights[positive]
    lookup = survey.set_index(["result_id", "policy", "group"])
    policy, rows = options["PRIMARY_POLICY"], []
    for contrast in options.get("CONTRASTS", []):
        for identifier, column in columns.items():
            a, b = contrast["GROUP_A"], contrast["GROUP_B"]
            left, right = lookup.loc[(identifier, policy, a)], lookup.loc[(identifier, policy, b)]
            df = int(min(left["df"], right["df"]))
            row = dict(contrast=contrast["ID"], result_id=identifier, unit=ci_get(dataset.column_metadata(column), "UNIT"),
                group_a=a, group_b=b, policy=policy, n_a=int(left.analysis_n), n_b=int(right.analysis_n),
                mean_a=left["mean"], mean_b=right["mean"], difference=np.nan, se=np.nan, ci95_low=np.nan, ci95_high=np.nan,
                df=df, status="empty_group", method="Taylor linearized difference; shared-PSU covariance retained; unadjusted CI")
            if row["n_a"] and row["n_b"]:
                y = values[identifier, policy][0][positive]
                usable = eligibility[identifier][0][positive] & np.isfinite(y)
                ma = usable & groups[a][0][positive]
                mb = usable & groups[b][0][positive]
                # Linearize both domain ratios jointly before the library estimates
                # the total-score variance, retaining the covariance and all PSUs.
                score = np.where(ma, y-left["mean"], 0.)/weights[ma].sum() - np.where(mb, y-right["mean"], 0.)/weights[mb].sum()
                estimator = TaylorEstimator(PopParam.total)
                estimator.estimate(y=score, samp_weight=weights, stratum=strata, psu=psus, remove_nan=False)
                difference, se = float(left["mean"]-right["mean"]), float(estimator.stderror)
                row.update(difference=difference, se=se, status="ok" if df > 0 else "insufficient_domain_df")
                if df > 0:
                    margin = float(t.ppf(.975, df))*se
                    row.update(ci95_low=difference-margin, ci95_high=difference+margin)
            rows.append(row)
    return pd.DataFrame(rows)


def analyze_dataset(dataset, options=None):
    """Execute an explicit analysis plan without changing input observations."""
    from .analysis import validate_dataset
    declared = _declared_plan(dataset)
    options = deepcopy(declared if options is None else options)
    columns, eligibility, groups = _plan(dataset, options)
    metadata = deepcopy(dataset.meta)
    for key in list(metadata):
        if str(key).upper() in {"ANALYSIS_PLAN", "CLINICAL_ANALYSIS"}:
            del metadata[key]
    metadata["ANALYSIS_PLAN"] = options
    dataset = dataset.replace(meta=metadata)
    issues = [issue for issue in validate_dataset(dataset).issues if issue.severity == "error"]
    if issues:
        raise ValueError("Analysis analysis requires valid input: " + issues[0].message)
    policies = options["POLICIES"]
    values = {(identifier, policy): _values(dataset, column, policy) for identifier, column in columns.items() for policy in policies}
    survey = _survey(dataset, columns, eligibility, groups, values, policies) if options["MODE"] == "SURVEY" else pd.DataFrame()
    positive = np.ones(len(dataset.df), dtype=bool)
    if options["MODE"] == "SURVEY":
        weight_id = ci_get(ci_get(dataset.meta, "SURVEY"), "WEIGHT_ID")
        positive = strict_numbers(dataset.df[resolve_id(dataset, weight_id).name], "Weights").gt(0).to_numpy()
    summaries, histograms, charts = [], [], []
    for identifier, column in columns.items():
        eligible, eligibility_missing = eligibility[identifier]
        unit = ci_get(dataset.column_metadata(column), "UNIT")
        for policy in policies:
            numeric, censored, available = values[identifier, policy]
            for group, (mask, _) in groups.items():
                all_eligible, domain = mask & eligible, mask & eligible & positive
                usable = domain & np.isfinite(numeric)
                source_missing = domain & ~np.isfinite(numeric) & ~censored
                row = dict(result_id=identifier, column=column.name, unit=unit, group=group, policy=policy,
                    eligible_n=int(all_eligible.sum()), zero_weight_eligible_n=int((all_eligible & ~positive).sum()),
                    domain_n=int(domain.sum()), analysis_n=int(usable.sum()), missing_n=int(source_missing.sum()),
                    censored_n=int((domain & censored).sum()), policy_excluded_n=int((domain & censored & ~np.isfinite(numeric)).sum()),
                    eligibility_missing_n=int((mask & eligibility_missing & positive).sum()), censoring_status=available,
                    estimand="unweighted complete-case sample", status="ok" if usable.any() else "empty_domain")
                row.update(_sample_summary(numeric[usable]))
                summaries.append(row)
                if identifier in options.get("PLOT_RESULT_IDS", columns) and group == options.get("PLOT_GROUP", "ALL") and policy == options["PRIMARY_POLICY"] and usable.any():
                    scale = options.get("HISTOGRAM_SCALES", {}).get(identifier, "linear")
                    plot_values = numeric[usable]
                    if scale == "log10":
                        if np.any(plot_values <= 0):
                            raise ValueError("Log histogram requires positive values; no nonpositive observations are silently dropped")
                        plot_values = np.log10(plot_values)
                    counts, edges = np.histogram(plot_values, bins=options.get("HISTOGRAM_BINS", 20))
                    if scale == "log10":
                        edges = np.power(10., edges)
                    records = [dict(result_id=identifier, group=group, policy=policy, unit=unit, lower=float(edges[i]), upper=float(edges[i+1]),
                        scale=scale, last_bin_closed=i==len(counts)-1, count=int(count), interval=f"{edges[i]:.3g} to {edges[i+1]:.3g}") for i, count in enumerate(counts)]
                    histograms.extend(records)
                    charts.append(chart_spec(f"histogram_{len(charts)+1}", title=f"{identifier} ({unit}): {group}, {policy}, unweighted distribution",
                        x="interval", y="count", rows=records, max_points=len(records)))
                    charts[-1].update(X_LABEL=f"Result ({unit}); {scale}-spaced bins", Y_LABEL="Unweighted count")
    summary = pd.DataFrame(summaries)
    correlations = []
    if options.get("PAIRS"):
        from scipy.stats import pearsonr, spearmanr
        for pair in options["PAIRS"]:
            x_id, y_id = pair["X_ID"], pair["Y_ID"]
            for policy in policies:
                x, y = values[x_id, policy][0], values[y_id, policy][0]
                eligible = eligibility[x_id][0] & eligibility[y_id][0] & positive
                for group, (mask, _) in groups.items():
                    domain = eligible & mask
                    usable = domain & np.isfinite(x) & np.isfinite(y)
                    xs, ys = x[usable], y[usable]
                    status = "insufficient_pairs" if len(xs) < 3 else "constant_variable" if np.ptp(xs) == 0 or np.ptp(ys) == 0 else "ok"
                    row = dict(pair=pair["ID"], x_id=x_id, y_id=y_id, x_unit=ci_get(dataset.column_metadata(columns[x_id]), "UNIT"),
                        y_unit=ci_get(dataset.column_metadata(columns[y_id]), "UNIT"), group=group, policy=policy,
                        eligible_pair_n=int(domain.sum()), paired_n=int(usable.sum()), excluded_n=int((domain & ~usable).sum()),
                        pearson_r=np.nan, spearman_rho=np.nan, status=status, estimand="unweighted pairwise-complete sample; no population p-value")
                    if status == "ok":
                        row.update(pearson_r=float(pearsonr(xs, ys).statistic), spearman_rho=float(spearmanr(xs, ys).statistic))
                    correlations.append(row)
                    if group == options.get("PLOT_GROUP", "ALL") and policy == options["PRIMARY_POLICY"] and len(xs):
                        records = [{"x": float(a), "y": float(b)} for a, b in zip(xs, ys)]
                        chart = chart_spec(f"scatter_{len(charts)+1}", type="scatter", title=f"{x_id} ({row['x_unit']}) vs {y_id} ({row['y_unit']}): {group}, {policy}; unweighted n={len(xs)}",
                            x="x", y="y", rows=records, max_points=len(records))
                        chart.update(X_LABEL=f"{x_id} ({row['x_unit']})", Y_LABEL=f"{y_id} ({row['y_unit']})")
                        charts.append(chart)
    if not survey.empty:
        for identifier, column in columns.items():
            if identifier not in options.get("PLOT_RESULT_IDS", columns):
                continue
            rows = survey.loc[survey.result_id.eq(identifier) & survey.policy.eq(options["PRIMARY_POLICY"]) & survey.status.eq("ok")]
            if len(rows):
                chart = chart_spec(f"survey_mean_{len(charts)+1}", type="interval", title=f"{identifier} ({ci_get(dataset.column_metadata(column), 'UNIT')}): survey mean and 95% CI, {options['PRIMARY_POLICY']}",
                    x="group", y="mean", rows=rows[["group", "mean", "ci95_low", "ci95_high"]].to_dict("records"), max_points=len(rows))
                chart.update(Y_LOW="ci95_low", Y_HIGH="ci95_high")
                chart.update(X_LABEL="Group (may overlap)", Y_LABEL=ci_get(dataset.column_metadata(column), "UNIT"))
                charts.append(chart)
    group_table = pd.DataFrame([dict(group=name, all_rows_n=int(mask.sum()), positive_weight_n=int((mask & positive).sum()),
        any_condition_missing_n=int(missing.sum()), definition="ALL" if name=="ALL" else json.dumps(options["GROUPS"][name], sort_keys=True)) for name, (mask, missing) in groups.items()])
    warnings = ["Sample percentiles are not clinical reference intervals; no healthy reference population was selected.",
        "Correlations, histograms, SD and CV describe the sample; they are not survey-adjusted inference or analytical QC imprecision.",
        "Censoring substitutions are sensitivity policies, not recovered concentrations; outliers are retained.",
        "Groups may overlap. Reports do not anonymize their inputs or establish diagnostic thresholds."]
    if options["MODE"] == "SURVEY":
        warnings += ["Full positive-weight design retained. Complete-case domains receive no extra item-nonresponse adjustment.",
            "Contrast intervals are unadjusted, primary-policy comparisons, not age-adjusted regression or multiplicity-adjusted tests."]
    from .observation_contract import observation_info, result_status
    observation = observation_info(dataset)
    warnings.append("Observation unit: " + observation["row_unit"] + "; rows are not automatically person counts.")
    tables = {"groups": group_table, "sample_summary": summary, "histograms": pd.DataFrame(histograms), "correlations": pd.DataFrame(correlations)}
    if ci_get(dataset.meta, "OBSERVATION", None) is not None:
        tables["observations"] = pd.DataFrame([observation])
    status_rows = []
    for identifier, column in columns.items():
        if ci_get(dataset.column_metadata(column), "RESULT_CONTEXT", None) is not None:
            accepted, reasons = result_status(dataset, column)
            status_rows.extend(dict(result_id=identifier, reason=reason or "ACCEPTED", count=int(count))
                for reason, count in reasons.value_counts().items())
    if status_rows:
        tables["result_status"] = pd.DataFrame(status_rows)
    from .analysis_models import fit_models, missingness_bounds
    if options.get("MODELS"):
        tables["models"] = fit_models(dataset, options, columns, eligibility, groups, values)
    if options.get("MISSINGNESS"):
        tables["missingness_bounds"] = missingness_bounds(dataset, options, columns, eligibility, groups, values)
    if not survey.empty:
        tables["survey_means"] = survey
        if options.get("CONTRASTS"):
            tables["survey_contrasts"] = _contrasts(dataset, options, columns, eligibility, groups, values, survey)
    return OperationOutput(name="ANALYZE", dataset=dataset, tables=tables, charts=charts, warnings=warnings,
        message=f"mode={options['MODE']} results={len(columns)} groups={len(groups)} policies={len(policies)}; observations unchanged")


def write_analysis_report(output, directory, *, docx=False):
    """Write aggregates and plots to a new directory; never overwrite an existing report."""
    from .reporting import render_chart_png, write_docx_report
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Analysis output directory already exists; choose a new directory")
    directory.mkdir(parents=True)
    files = []
    for name, table in (output.tables or {}).items():
        path = directory / (name + ".csv")
        table.to_csv(path, index=False)
        files.append(path)
    for index, chart in enumerate(output.charts or [], 1):
        files.append(render_chart_png(chart, output.tables or {}, directory / f"figure_{index:02d}.png"))
    if docx:
        # Do not implicitly print the first rows of an identified input dataset.
        report_output = OperationOutput(name=output.name, tables=_report_tables(output), charts=output.charts, warnings=output.warnings, message=output.message)
        files.append(write_docx_report(directory / "analysis_report.docx", title="Data analysis", outputs=[report_output], max_table_rows=40))
    dataset = output.dataset
    plan = ci_get(dataset.meta, "ANALYSIS_PLAN", {})
    source = Path(dataset.source_path) if dataset.source_path else None
    packages = {}
    for name in ["tametools", "numpy", "pandas", "scipy", "samplics", "statsmodels", "scikit-learn", "pint", "matplotlib", "python-docx"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not installed as a distribution"
    from .cellstate import serialize_cell
    records = [[str(serialize_cell(v)) for v in row] for row in dataset.df.itertuples(index=False, name=None)]
    from .execution_audit import effective_input_snapshot, canonical_json, source_hashes
    snapshot = effective_input_snapshot(dataset)
    snapshot_path = directory / "effective_input.json"
    snapshot_path.write_text(canonical_json(snapshot) + "\n", encoding="utf-8")
    files.append(snapshot_path)
    manifest = dict(plan=plan, warnings=output.warnings, input_rows=len(dataset.df),
        runtime=sys.version, platform=platform.platform(),
        resolved_result_ids=output.tables["sample_summary"].result_id.drop_duplicates().tolist(),
        source_file_sha256=hashlib.sha256(source.read_bytes()).hexdigest() if source and source.is_file() else None,
        input_cell_sha256=hashlib.sha256(json.dumps(records, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest(),
        plan_sha256=hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=True).encode()).hexdigest(), packages=packages,
        effective_input_sha256=hashlib.sha256(canonical_json(snapshot).encode()).hexdigest(),
        effective_input_snapshot=snapshot_path.name,
        resolved_measurements={identifier: dataset.column_metadata(resolve_id(dataset, identifier))
            for identifier in output.tables["sample_summary"].result_id.drop_duplicates()},
        source_modules_sha256=source_hashes(),
        table_rows={name: len(table) for name, table in output.tables.items()},
        files={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n")
    return [str(path) for path in [*files, path]]


def _report_tables(output):
    """Compact primary-policy views; full-precision tables remain in the CSVs."""
    primary = ci_get(output.dataset.meta, "ANALYSIS_PLAN")["PRIMARY_POLICY"]
    tables = {}
    summary = output.tables["sample_summary"]
    for identifier, frame in summary.loc[summary.policy.eq(primary)].groupby("result_id", sort=False):
        units = frame.unit.iloc[0]
        selected = frame[["group", "analysis_n", "mean", "sd", "median"]].copy()
        selected["Q1-Q3"] = [f"{a:.4g} to {b:.4g}" for a,b in zip(frame.q25, frame.q75)]
        tables[f"{identifier} ({units}): unweighted, {primary}"] = selected
        tables[f"{identifier}: denominators"] = frame[["group", "domain_n", "analysis_n", "missing_n", "censored_n", "policy_excluded_n"]]
    survey = output.tables.get("survey_means")
    if survey is not None:
        for identifier, frame in survey.loc[survey.policy.eq(primary)].groupby("result_id", sort=False):
            selected = frame[["group", "analysis_n", "mean", "se", "df"]].copy()
            selected["95% CI"] = [f"{a:.4g} to {b:.4g}" for a,b in zip(frame.ci95_low, frame.ci95_high)]
            tables[f"{identifier} ({frame.unit.iloc[0]}): survey mean, {primary}"] = selected
    correlations = output.tables["correlations"]
    if not correlations.empty:
        for pair, frame in correlations.loc[correlations.policy.eq(primary)].groupby("pair", sort=False):
            tables[f"{pair}: unweighted correlation, {primary}"] = frame[["group", "paired_n", "pearson_r", "spearman_rho", "status"]]
    contrasts = output.tables.get("survey_contrasts")
    if contrasts is not None:
        for name, frame in contrasts.groupby("contrast", sort=False):
            selected = frame[["result_id", "unit", "difference", "se", "df"]].copy()
            selected["95% CI"] = [f"{a:.4g} to {b:.4g}" for a,b in zip(frame.ci95_low, frame.ci95_high)]
            tables[f"{name}: survey mean difference, {primary}, unadjusted"] = selected
    for key in ("observations", "result_status", "models", "missingness_bounds"):
        if key in output.tables:
            tables[key] = output.tables[key].copy()
    for frame in tables.values():
        for column in frame.select_dtypes(include="floating").columns:
            frame[column] = frame[column].map(lambda v: f"{v:.4g}" if np.isfinite(v) else "")
    return tables
