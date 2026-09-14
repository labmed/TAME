"""Design-aware descriptive means using the optional samplics engine."""
from __future__ import annotations

from importlib.metadata import version

import numpy as np
import pandas as pd

from .analysis_contract import check_contract_version, measurement_values, resolve_id, strict_numbers
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import OperationOutput, TameDataset
from .measurement_tags import require_measurement_tags, require_quantitative


def survey_describe(dataset: TameDataset, options: dict | None = None) -> OperationOutput:
    require_measurement_tags(dataset)
    from .observation_contract import observation_info
    observation_info(dataset, inference=True)
    check_contract_version(dataset)
    config = ci_get(dataset.meta, "SURVEY", {})
    options = ci_get(dataset.meta, "SURVEY_DESCRIBE", {}) if options is None else options
    if not isinstance(config, dict) or not isinstance(options, dict):
        raise ValueError("SURVEY and SURVEY_DESCRIBE must be tables")
    supported = {"DESIGN", "WEIGHT_ID", "STRATUM_ID", "PSU_ID", "EXPECTED_ROWS"}
    if set(str(k).upper() for k in config) - supported:
        raise ValueError("Unsupported SURVEY setting; FPC and replicate designs are not implemented")
    if ci_get(config, "DESIGN") != "STRATIFIED_CLUSTER_WR":
        raise ValueError("SURVEY.DESIGN must be STRATIFIED_CLUSTER_WR")
    expected = ci_get(config, "EXPECTED_ROWS", None)
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected != len(dataset.df)):
        raise ValueError("SURVEY.EXPECTED_ROWS mismatch: retain the full design, use domains instead of filtering rows")
    if set(str(k).upper() for k in options) - {"RESULT_IDS", "DOMAIN_IDS", "POLICIES"}:
        raise ValueError("Unknown SURVEY_DESCRIBE option")
    result_ids = ci_get(options, "RESULT_IDS", [])
    domain_ids = ci_get(options, "DOMAIN_IDS", [])
    policies = ci_get(options, "POLICIES", ["RELEASED"])
    for label, values in (("RESULT_IDS", result_ids), ("DOMAIN_IDS", domain_ids), ("POLICIES", policies)):
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values) or len(values) != len(set(values)):
            raise ValueError(f"{label} must be a list of distinct strings")
    if not result_ids or not policies or not set(policies) <= {"RELEASED", "VALUE", "DELETE"}:
        raise ValueError("Specify result IDs and policies RELEASED, VALUE or DELETE")
    weight_col = resolve_id(dataset, ci_get(config, "WEIGHT_ID"))
    strata_col = resolve_id(dataset, ci_get(config, "STRATUM_ID"))
    psu_col = resolve_id(dataset, ci_get(config, "PSU_ID"))
    if len({weight_col.name, strata_col.name, psu_col.name}) != 3:
        raise ValueError("Weight, stratum and PSU must be distinct columns")
    weights = strict_numbers(dataset.df[weight_col.name], "Survey weights")
    if weights.isna().any() or (weights < 0).any():
        raise ValueError("Survey weights must be finite and nonnegative, with no missing values")
    positive = weights.gt(0).to_numpy()
    if not positive.any():
        raise ValueError("At least one positive survey weight is required")
    design = dataset.df.loc[positive, [strata_col.name, psu_col.name]].copy()
    if not all(cell_state(v) == STATE_VALUE for row in design.itertuples(index=False, name=None) for v in row):
        raise ValueError("Positive-weight records require stratum and PSU identifiers")
    strata = pd.factorize(design[strata_col.name].astype(str), sort=True)[0]
    # NHANES reuses PSU labels in different strata; encode the pair, not PSU alone.
    psus = pd.factorize(pd.MultiIndex.from_frame(design.astype(str)), sort=True)[0]
    pairs = pd.DataFrame({"stratum": strata, "psu": psus}).drop_duplicates()
    if pairs.groupby("stratum").size().lt(2).any():
        raise ValueError("Singleton stratum in full design: no automatic lonely-PSU adjustment")
    domains = [("ALL", np.ones(len(dataset.df), dtype=bool))]
    for identifier in domain_ids:
        column = resolve_id(dataset, identifier)
        values = strict_numbers(dataset.df[column.name], f"Domain {identifier}")
        if values.isna().any() or not values.isin([0, 1]).all():
            raise ValueError(f"Domain {identifier} must contain only 0 or 1, without missing values")
        domains.append((identifier, values.eq(1).to_numpy()))
    try:
        from samplics.estimation import TaylorEstimator
        from samplics.utils.types import PopParam
        from scipy.stats import t
    except ImportError as exc:
        raise ImportError("SURVEY_DESCRIBE requires tametools[survey] (samplics 0.4.55)") from exc
    if version("samplics") != "0.4.55":
        raise RuntimeError("This survey adapter is validated with samplics==0.4.55; install tametools[survey]")
    rows = []
    warnings = ["Descriptive complete-case domain estimates; item nonresponse is not reweighted.",
                "Censoring substitutions are sensitivity policies, not recovered concentrations."]
    w = weights.to_numpy()[positive]
    for identifier in result_ids:
        column = resolve_id(dataset, identifier)
        require_quantitative(dataset, column, require_unit=True)
        if not dataset.column_has_tag(column, "RESULT"):
            raise ValueError(f"{identifier} must identify a RESULT column")
        unit = ci_get(dataset.column_metadata(column), "UNIT", None)
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"{identifier} requires a declared UNIT")
        eligible_id = ci_get(dataset.column_metadata(column), "ELIGIBLE_ID", None)
        from .planned_analysis import _eligibility
        eligible = _eligibility(dataset, column)[0]
        if eligible_id is not None:
            eligible_column = resolve_id(dataset, eligible_id)
            eligibility = strict_numbers(dataset.df[eligible_column.name], f"Eligibility {eligible_id}")
            if eligibility.isna().any() or not eligibility.isin([0, 1]).all():
                raise ValueError(f"Eligibility {eligible_id} must contain only 0 or 1")
            eligible &= eligibility.eq(1).to_numpy()
        for policy in policies:
            values, below = measurement_values(dataset, column, policy)
            for domain_id, domain in domains:
                in_domain = (domain & eligible)[positive]
                y_values = values.to_numpy()[positive]
                usable = in_domain & np.isfinite(y_values)
                censored = below.to_numpy()[positive] & in_domain
                missing = in_domain & ~np.isfinite(y_values) & ~censored
                represented = pairs.iloc[:0] if not usable.any() else pd.DataFrame({"stratum": strata[usable], "psu": psus[usable]}).drop_duplicates()
                df = len(represented) - represented.stratum.nunique()
                row = {"result_id": identifier, "column": column.name, "unit": unit,
                       "policy": policy, "domain": domain_id, "eligible_id": eligible_id,
                       "design_n": int(positive.sum()),
                       "zero_weight_n": int((~positive).sum()), "design_strata": pairs.stratum.nunique(),
                       "design_psus": len(pairs), "domain_n": int(in_domain.sum()),
                       "analysis_n": int(usable.sum()), "missing_n": int(missing.sum()),
                       "censoring_flag_available": ci_get(dataset.column_metadata(column), "CENSORING", None) is not None,
                       "below_llod_n": int(censored.sum()), "weight_sum": float(w[usable].sum()),
                       "df": int(df), "mean": np.nan, "se": np.nan,
                       "ci95_low": np.nan, "ci95_high": np.nan, "status": "empty_domain",
                       "engine": "samplics 0.4.55", "variance_method": "Taylor WR ratio"}
                if usable.any():
                    # A domain mean is a ratio of totals. Zero numerator/denominator
                    # contributions retain out-of-domain PSUs in the variance calculation.
                    estimator = TaylorEstimator(PopParam.ratio)
                    estimator.estimate(y=np.where(usable, y_values, 0.0), x=usable.astype(float),
                                       samp_weight=w, stratum=strata, psu=psus, remove_nan=False)
                    mean, se = float(estimator.point_est), float(estimator.stderror)
                    row.update(mean=mean, se=se, status="ok" if df > 0 else "insufficient_domain_df")
                    if df > 0:
                        margin = float(t.ppf(.975, df)) * se
                        row.update(ci95_low=mean - margin, ci95_high=mean + margin)
                rows.append(row)
    return OperationOutput(name="SURVEY_DESCRIBE", dataset=dataset, table=pd.DataFrame(rows),
                           warnings=warnings, message=f"survey means={len(rows)}; full-design rows={int(positive.sum())}")
