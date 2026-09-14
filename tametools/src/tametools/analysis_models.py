"""Declared linear adjustment and bounded missing-outcome sensitivity analysis."""
from importlib.metadata import version

import numpy as np
import pandas as pd

from .analysis_contract import resolve_id, strict_numbers
from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .measurement_tags import require_quantitative
from .observation_contract import key_frame, observation_info


def validate_extensions(dataset, plan, columns, groups):
    from .planned_analysis import _table, _id, _number
    for key in ("MODELS", "MISSINGNESS"):
        entries = plan.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(key + " must be an array of tables")
        used = set()
        for entry in entries:
            if key == "MODELS":
                _table(entry, {"ID", "Y_ID", "COVARIANCE", "POLICY"},
                    {"NUMERIC_IDS", "CATEGORICAL", "GROUP", "CLUSTER_ID"}, key)
            else:
                _table(entry, {"ID", "RESULT_ID", "LOWER", "UPPER", "POLICY", "ASSUMPTION"}, {"GROUP"}, key)
            identifier = _id(entry["ID"])
            if identifier in used:
                raise ValueError("Duplicate " + key + " ID")
            used.add(identifier)
            result_id = entry["Y_ID" if key == "MODELS" else "RESULT_ID"]
            if result_id not in columns or entry.get("GROUP", "ALL") not in groups or entry["POLICY"] not in plan["POLICIES"]:
                raise ValueError(key + " must reference declared results, groups and policies")
            if key == "MISSINGNESS":
                if _number(entry["LOWER"]) >= _number(entry["UPPER"]) or not isinstance(entry["ASSUMPTION"], str) or not entry["ASSUMPTION"].strip():
                    raise ValueError("MISSINGNESS requires increasing finite bounds and an explicit ASSUMPTION")
                continue
            covariance = entry["COVARIANCE"]
            if covariance not in {"HC3", "CLUSTER", "SURVEY"} or (covariance == "SURVEY") != (plan["MODE"] == "SURVEY"):
                raise ValueError("Model COVARIANCE must be HC3/CLUSTER for SAMPLE or SURVEY for survey input")
            if ("CLUSTER_ID" in entry) != (covariance == "CLUSTER"):
                raise ValueError("Only CLUSTER covariance requires CLUSTER_ID")
            if covariance == "CLUSTER":
                key_frame(dataset, [entry["CLUSTER_ID"]])
            observation_info(dataset, inference=True, cluster_id=entry.get("CLUSTER_ID"))
            numeric = entry.get("NUMERIC_IDS", [])
            categorical = entry.get("CATEGORICAL", [])
            if not isinstance(numeric, list) or any(not isinstance(v, str) for v in numeric) or len(set(numeric)) != len(numeric) or not isinstance(categorical, list):
                raise ValueError("Invalid model covariate lists")
            ids = list(numeric)
            for identifier in numeric:
                column = resolve_id(dataset, identifier)
                require_quantitative(dataset, column)
                if dataset.column_has_tag(column, "<NUM>") or ci_get(dataset.column_metadata(column), "CENSORING", None) is not None or ci_get(dataset.column_metadata(column), "CENSORING_INTERVAL", None) is not None:
                    raise ValueError("Censored covariates need a separately reviewed derived column; no implicit substitution")
            for declaration in categorical:
                _table(declaration, {"COLUMN_ID", "LEVELS", "REFERENCE"}, label="model categorical covariate")
                resolve_id(dataset, declaration["COLUMN_ID"])
                levels = declaration["LEVELS"]
                if not isinstance(levels, list) or len(levels) < 2 or any(not isinstance(v, str) for v in levels) or len(set(levels)) != len(levels) or declaration["REFERENCE"] not in levels:
                    raise ValueError("Categorical model covariates need distinct exact text levels and a reference")
                ids.append(declaration["COLUMN_ID"])
            if not ids or result_id in ids or len(set(ids)) != len(ids):
                raise ValueError("Declare distinct covariates, separate from the outcome")


def fit_models(dataset, plan, columns, eligibility, groups, values):
    from scipy.stats import t
    from .planned_analysis import _eligibility
    import statsmodels.api as sm
    rows = []
    for model in plan.get("MODELS", []):
        identifier, policy = model["Y_ID"], model["POLICY"]
        y = values[identifier, policy][0]
        domain = eligibility[identifier][0] & groups[model.get("GROUP", "ALL")][0]
        x = {"Intercept": np.ones(len(y))}
        covariate_ok = np.ones(len(y), dtype=bool)
        for cid in model.get("NUMERIC_IDS", []):
            column = resolve_id(dataset, cid)
            x[cid] = strict_numbers(dataset.df[column.name], "Model " + cid).to_numpy()
            covariate_ok &= _eligibility(dataset, column)[0]
        for categorical in model.get("CATEGORICAL", []):
            column = resolve_id(dataset, categorical["COLUMN_ID"])
            raw = dataset.df[column.name]
            present = raw.map(lambda v: cell_state(v) == STATE_VALUE).to_numpy()
            texts = raw.astype(str)
            if (present & ~texts.isin(categorical["LEVELS"]).to_numpy()).any():
                raise ValueError("Unknown categorical covariate code: " + categorical["COLUMN_ID"])
            covariate_ok &= present & _eligibility(dataset, column)[0]
            for level in categorical["LEVELS"]:
                if level != categorical["REFERENCE"]:
                    x[categorical["COLUMN_ID"] + "=" + level] = texts.eq(level).astype(float).to_numpy()
        matrix = pd.DataFrame(x).to_numpy(float)
        usable = domain & covariate_ok & np.isfinite(y) & np.isfinite(matrix).all(axis=1)
        covariance = model["COVARIANCE"]
        weights, strata, psus = None, None, None
        positive = np.ones(len(y), dtype=bool)
        design_n = len(y)
        if covariance == "SURVEY":
            design = ci_get(dataset.meta, "SURVEY")
            weights = strict_numbers(dataset.df[resolve_id(dataset, design["WEIGHT_ID"]).name], "Weights").to_numpy()
            positive = weights > 0
            usable &= positive
            design_n = int(positive.sum())
            labels = dataset.df.loc[positive, [resolve_id(dataset, design[key]).name for key in ("STRATUM_ID", "PSU_ID")]].astype(str)
            strata = pd.factorize(labels.iloc[:, 0], sort=True)[0]
            psus = pd.factorize(pd.MultiIndex.from_frame(labels), sort=True)[0]
        n, p = int(usable.sum()), matrix.shape[1]
        if n <= p or np.linalg.matrix_rank(matrix[usable]) != p:
            raise ValueError("Model has insufficient complete cases or a rank-deficient design: " + model["ID"])
        cluster_n = None
        if covariance == "SURVEY":
            if version("samplics") != "0.4.55":
                raise RuntimeError("Survey model adapter requires samplics==0.4.55")
            from samplics.regression import SurveyGLM
            from samplics.utils.types import ModelType
            estimator = SurveyGLM(ModelType.LINEAR)
            active = usable[positive]
            # Zero contribution rows retain every positive-weight stratum/PSU in the sandwich.
            estimator.estimate(y=np.where(active, y[positive], 0.),
                x=np.where(active[:, None], matrix[positive], 0.), x_labels=list(x),
                samp_weight=np.where(active, weights[positive], 0.), stratum=strata, psu=psus,
                add_intercept=False, remove_nan=False)
            beta, se = np.asarray(estimator.beta["point_est"]), np.asarray(estimator.beta["stderror"])
            represented = pd.DataFrame({"stratum": strata[active], "psu": psus[active]}).drop_duplicates()
            df = len(represented) - represented.stratum.nunique()
            engine = "samplics 0.4.55 SurveyGLM; design-df t interval"
        else:
            fit = sm.OLS(y[usable], matrix[usable])
            if covariance == "CLUSTER":
                clusters = key_frame(dataset, [model["CLUSTER_ID"]]).iloc[:, 0].to_numpy()[usable]
                cluster_n = len(np.unique(clusters))
                if cluster_n < 2:
                    raise ValueError("Cluster inference requires at least two represented clusters")
                fitted = fit.fit(cov_type="cluster", cov_kwds={"groups": clusters, "use_correction": True}, use_t=True)
                df = cluster_n - 1
            else:
                fitted = fit.fit(cov_type="HC3", use_t=True)
                df = n - p
            beta, se = np.asarray(fitted.params), np.asarray(fitted.bse)
            engine = "statsmodels " + version("statsmodels") + " OLS"
        if not np.isfinite(beta).all() or not np.isfinite(se).all():
            raise ValueError("Model produced nonfinite coefficients or uncertainty")
        critical = float(t.ppf(.975, df)) if df > 0 else np.nan
        for term, coefficient, error in zip(x, beta, se):
            rows.append(dict(model=model["ID"], result_id=identifier, unit=ci_get(dataset.column_metadata(columns[identifier]), "UNIT"),
                group=model.get("GROUP", "ALL"), policy=policy, term=term, coefficient=float(coefficient), se=float(error),
                ci95_low=coefficient-critical*error, ci95_high=coefficient+critical*error, df=int(df),
                covariance=covariance, input_n=len(y), design_n=design_n, domain_n=int(domain.sum()),
                positive_weight_domain_n=int((domain & positive).sum()), zero_weight_domain_n=int((domain & ~positive).sum()),
                outcome_missing_n=int((domain & positive & ~np.isfinite(y)).sum()),
                covariate_excluded_n=int((domain & positive & np.isfinite(y) & (~covariate_ok | ~np.isfinite(matrix).all(axis=1))).sum()),
                analysis_n=n, excluded_n=int(domain.sum())-n, cluster_n=cluster_n,
                status="ok" if df > 0 else "insufficient_design_df", engine=engine,
                estimand="complete-case adjusted linear association; not causal; no item-nonresponse adjustment"))
    return pd.DataFrame(rows)


def missingness_bounds(dataset, plan, columns, eligibility, groups, values):
    rows = []
    weights = np.ones(len(dataset.df))
    if plan["MODE"] == "SURVEY":
        design = ci_get(dataset.meta, "SURVEY")
        weights = strict_numbers(dataset.df[resolve_id(dataset, design["WEIGHT_ID"]).name], "Weights").to_numpy()
    for entry in plan.get("MISSINGNESS", []):
        identifier, policy = entry["RESULT_ID"], entry["POLICY"]
        domain = eligibility[identifier][0] & groups[entry.get("GROUP", "ALL")][0] & (weights > 0)
        y = values[identifier, policy][0]
        observed, missing = domain & np.isfinite(y), domain & ~np.isfinite(y)
        denominator = float(weights[domain].sum())
        total = float(np.sum(weights[observed] * y[observed]))
        missing_weight = float(weights[missing].sum())
        rows.append(dict(sensitivity=entry["ID"], result_id=identifier, policy=policy, group=entry.get("GROUP", "ALL"),
            unit=ci_get(dataset.column_metadata(columns[identifier]), "UNIT"), domain_n=int(domain.sum()),
            observed_n=int(observed.sum()), missing_n=int(missing.sum()),
            missing_weight_fraction=missing_weight/denominator if denominator else np.nan,
            observed_mean=total/weights[observed].sum() if observed.any() else np.nan,
            mean_lower=(total+entry["LOWER"]*missing_weight)/denominator if denominator else np.nan,
            mean_upper=(total+entry["UPPER"]*missing_weight)/denominator if denominator else np.nan,
            assumed_missing_lower=entry["LOWER"], assumed_missing_upper=entry["UPPER"], assumption=entry["ASSUMPTION"],
            estimand="all missing outcomes within declared bounds; sensitivity bounds, not confidence intervals",
            weighting="survey" if plan["MODE"] == "SURVEY" else "unweighted",
            status="ok" if denominator else "empty_domain"))
    return pd.DataFrame(rows)
