"""Explicit paired method comparison; not an EP09 compliance certification."""
import numpy as np
import pandas as pd

from .analysis import numeric_series_for
from .analysis_contract import resolve_id
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .measurement_tags import require_quantitative
from .observation_contract import key_frame, observation_info


def compare_methods(dataset, options, results, method_column, test_column, keys):
    from scipy.stats import t
    from .plugins.clinical_stats import _deming, _passing_bablok
    if ci_get(dataset.meta, "SURVEY", None) is not None:
        raise ValueError("Method comparison does not implement survey-weighted inference")
    observation_info(dataset, inference=True)
    if not results or method_column is None:
        raise ValueError("Method comparison requires results and METHOD_COLUMN")
    a, b = ci_get(options, "METHOD_A", None), ci_get(options, "METHOD_B", None)
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b or a == b:
        raise ValueError("Declare distinct METHOD_A (x/reference) and METHOD_B (y/comparison)")
    policy = ci_get(options, "DUPLICATES", "ERROR")
    if policy not in {"ERROR", "MEAN"}:
        raise ValueError("DUPLICATES must be ERROR or explicitly MEAN")
    lam = ci_get(options, "DEMING_LAMBDA", 1.0)
    if isinstance(lam, bool) or not isinstance(lam, (float, int)) or not np.isfinite(lam) or lam <= 0:
        raise ValueError("DEMING_LAMBDA is the positive error-variance ratio var(error_y)/var(error_x)")
    points = ci_get(options, "EVALUATION_CONCENTRATIONS", [])
    if not isinstance(points, list) or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not np.isfinite(v) for v in points):
        raise ValueError("EVALUATION_CONCENTRATIONS must contain finite numbers in the common UNIT")
    if ci_get(options, "KEY_IDS", None) is not None:
        if ci_get(options, "KEY", None) is not None:
            raise ValueError("Declare KEY_IDS or KEY, not both")
        key_data = key_frame(dataset, ci_get(options, "KEY_IDS"))
    else:
        if not keys:
            raise ValueError("Method comparison requires specimen/time pairing keys")
        from .plugins.clinical_stats import _resolve
        names = [_resolve(dataset, name).name for name in keys]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate pairing key")
        key_data = dataset.df[names].copy()
        if any(key_data[name].map(lambda v: cell_state(v) != STATE_VALUE or not str(v).strip()).any() for name in names):
            raise ValueError("Missing pairing key")
        key_data = key_data.astype(str)
    methods = dataset.df[method_column.name]
    if methods.map(lambda v: cell_state(v) != STATE_VALUE or not str(v).strip()).any():
        raise ValueError("Missing method identity")
    observed = set(methods.astype(str))
    if not {a, b} <= observed:
        raise ValueError("Selected methods are absent")
    extra = observed - {a, b}
    if extra and ci_get(options, "OTHER_METHODS", "ERROR") != "EXCLUDE":
        raise ValueError("Additional methods require explicit OTHER_METHODS=EXCLUDE")
    warnings = ["Exploratory method comparison; unadjusted bias CI assumes independent pairs. LoA are point estimates, not clinical acceptance limits."]
    if extra:
        warnings.append("Explicitly excluded methods: " + ", ".join(sorted(extra)))
    rows = []
    for result in results:
        require_quantitative(dataset, result, require_unit=True)
        unit = ci_get(dataset.column_metadata(result), "UNIT")
        unit_id = ci_get(dataset.column_metadata(result), "UNIT_ID", None)
        if unit_id is not None:
            units = dataset.df[resolve_id(dataset, unit_id).name]
            if not units.eq(unit).all():
                raise ValueError("Heterogeneous row units must be integrated before method comparison")
        work = key_data.copy()
        work["_method"] = methods.astype(str)
        comparator_policy = ci_get(options, "COMPARATOR_POLICY", "DELETE")
        if comparator_policy not in {"DELETE", "VALUE"}:
            raise ValueError("Method comparison COMPARATOR_POLICY must be DELETE or VALUE")
        work["_value"] = numeric_series_for(dataset, result, crr_policy=comparator_policy)
        work["_test"] = dataset.df[test_column.name] if test_column else result.name
        if work._test.map(lambda v: cell_state(v) != STATE_VALUE).any():
            raise ValueError("Missing analyte identity")
        work = work.loc[work._method.isin([a, b])]
        indexes = list(key_data.columns)
        for test, group in work.groupby("_test", sort=True, observed=True):
            duplicates = group.duplicated(indexes + ["_method"], keep=False)
            if duplicates.any() and policy == "ERROR":
                raise ValueError("Duplicate specimen/method results; explicitly select DUPLICATES=MEAN or refine keys")
            wide = group.groupby(indexes + ["_method"], observed=True)._value.mean().unstack("_method") if policy == "MEAN" else group.pivot(index=indexes, columns="_method", values="_value")
            wide = wide.reindex(columns=[a, b])
            paired = wide.dropna()
            row = dict(source_result_column=result.name, test=test, unit=unit, method_a=a, method_b=b,
                n=len(paired), input_n=len(group), duplicate_rows_n=int(duplicates.sum()),
                unpaired_n=int(wide.isna().any(axis=1).sum()), duplicates_policy=policy,
                deming_lambda=float(lam), comparator_policy=comparator_policy, status="insufficient_pairs")
            if len(paired) < 3:
                rows.append(row)
                continue
            x, y = paired[a].to_numpy(float), paired[b].to_numpy(float)
            pb_slope, pb_intercept = _passing_bablok(x, y)
            slope, intercept = _deming(x, y, lam=float(lam))
            diff = y - x
            bias, sd = float(diff.mean()), float(diff.std(ddof=1))
            margin = float(t.ppf(.975, len(diff)-1)) * sd / np.sqrt(len(diff))
            row.update(status="constant_method" if np.ptp(x) == 0 or np.ptp(y) == 0 else "ok",
                r=float(np.corrcoef(x, y)[0, 1]) if np.ptp(x) and np.ptp(y) else np.nan,
                pb_slope=pb_slope, pb_intercept=pb_intercept, deming_slope=slope, deming_intercept=intercept,
                bias=bias, bias_ci95_low=bias-margin, bias_ci95_high=bias+margin,
                sd_diff=sd, lower_loa=bias-1.96*sd, upper_loa=bias+1.96*sd)
            rows.append(row)
            for value in points:
                rows.append({**row, "evaluation_concentration": value,
                    "deming_predicted_bias": intercept + (slope-1)*value,
                    "evaluation_status": "interpolation" if x.min() <= value <= x.max() else "extrapolation"})
    return pd.DataFrame(rows), warnings
