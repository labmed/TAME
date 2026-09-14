"""Explicit EP28 reference-interval study plugin; legacy REFERENCE_INTERVAL is unchanged."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from itertools import combinations
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from tametools.analysis import age_series_for, parse_comparator_number
from tametools.analysis_contract import column_ids, measurement_values, resolve_id
from tametools.categories import normalize_categories
from tametools.cellstate import STATE_VALUE, cell_state
from tametools.config import ci_get
from tametools.measurement_tags import require_quantitative
from tametools.models import ColumnSpec, OperationOutput, TameDataset
from tametools.observation_contract import observation_info, result_status
from tametools.planned_analysis import _conditions, _eligibility
from tametools.plugin_base.base import register_plugin
from tametools.plugin_base.roles import RESULT_ROLE
from tametools.provenance import append_log_entry, operation_boundary, record_effective_parameters
from tametools.reporting import chart_spec, with_visualizations
from tametools import ri_ep28 as engine
from tametools import ri_study as study
from tametools import ri_study_workflow as study_flow


DEFAULTS = dict(METHOD="NONPARAMETRIC", METHODS=[], COVERAGE=.95, CI_LEVEL=.90,
    CI_METHOD="AUTO", QUANTILE_TYPE=6, BOOTSTRAP_N=2000, BOOTSTRAP_TYPE="PERCENTILE", SEED=20260911,
    OUTLIER_METHOD="NONE", OUTLIER_ACTION="FLAG", OUTLIER_METHODS=[], TUKEY_K=1.5,
    DIXON_ALPHA=.05, REED_ITERATIONS=10, REED_BLOCK_MAX=1, OUTLIER_WARN_RATE=.02, MIN_N=120, MIN_CALCULATE_N=3,
    CENSOR_POLICY="ERROR", RESULT_IDS=[], ITEM_ID="", SUBJECT_ID="", INCLUDE=[],
    PARTITION_BY=[], AGE_ID="", SEX_ID="", SEX_MAP={}, AGE_CUTS=[], PARTITION_SCALE="RAW",
    MAX_GROUPS=100, CI_WIDTH_WARN_RATIO=.20, POPULATION={}, VERIFY={}, VERIFY_BATCH=1,
    FIRST_BATCH_OUTSIDE=None, FIRST_BATCH_SUBJECT_IDS=[], OUTPUT_DIR="", REPORT_PATH="", REPORT_MAX_ROWS=200,
    PLOT_MAX_GROUPS=12, POINT_ESTIMATE="DIRECT", BOOTSTRAP_QUANTILE="TYPE7", BOXCOX={},
    NORMAL_Z=None, PARAMETRIC_TRIM_Z=None, LAVE={}, PARTITION_DIAGNOSTICS=[],
    REPORTING_UNITS={}, NORMALITY_TESTS=["SHAPIRO"])


def _sequence(value,label):
    if isinstance(value,str):
        value=[v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value,(list,tuple)):
        raise ValueError(label+" must be a list (or comma-separated values)")
    return list(value)


def _integer(value,label,minimum,maximum):
    if isinstance(value,bool) or not math.isfinite(float(value)) or float(value)!=int(value) or not minimum<=int(value)<=maximum:
        raise ValueError(f"{label} must be an integer between {minimum} and {maximum}")
    return int(value)


def _config(dataset,options):
    cfg=deepcopy(DEFAULTS)
    for section in [ci_get(dataset.meta,"RI_EP28",{}),ci_get(dataset.meta,"REFERENCE_INTERVAL_EP28",{}),options]:
        if not isinstance(section,dict):raise ValueError("RI_EP28 settings must be a table")
        for name,value in section.items():
            key=str(name).upper()
            if key not in cfg:raise ValueError("Unknown RI_EP28 option: "+key)
            cfg[key]=value
    for key in ["METHOD","CI_METHOD","BOOTSTRAP_TYPE","OUTLIER_METHOD","OUTLIER_ACTION","CENSOR_POLICY","PARTITION_SCALE","POINT_ESTIMATE","BOOTSTRAP_QUANTILE"]:
        cfg[key]=str(cfg[key]).strip().upper()
    for key in ["METHODS","OUTLIER_METHODS","RESULT_IDS","PARTITION_BY","AGE_CUTS","FIRST_BATCH_SUBJECT_IDS","PARTITION_DIAGNOSTICS","NORMALITY_TESTS"]:
        cfg[key]=_sequence(cfg[key],key)
    cfg["METHODS"]=list(dict.fromkeys([cfg["METHOD"],*[str(v).upper() for v in cfg["METHODS"]]]))
    cfg["OUTLIER_METHODS"]=list(dict.fromkeys([str(v).upper() for v in cfg["OUTLIER_METHODS"]]))
    if not set(cfg["METHODS"])<=engine.METHODS:raise ValueError("Unsupported reference-interval method")
    if cfg["CI_METHOD"] not in engine.CI_METHODS:raise ValueError("Unsupported CI_METHOD")
    if cfg["OUTLIER_METHOD"] not in engine.OUTLIERS or not set(cfg["OUTLIER_METHODS"])<=engine.OUTLIERS:
        raise ValueError("Use NONE, TUKEY, LOG_TUKEY, HORN, DIXON_Q, REED or COOK; DIXON is ambiguous")
    if cfg["OUTLIER_ACTION"] not in {"FLAG","REMOVE"}:raise ValueError("OUTLIER_ACTION must be FLAG or REMOVE")
    if cfg["CENSOR_POLICY"] not in {"ERROR","EXCLUDE","VALUE","RELEASED"}:raise ValueError("Invalid CENSOR_POLICY")
    if cfg["PARTITION_SCALE"] not in {"RAW","LOG"}:raise ValueError("PARTITION_SCALE must be RAW or LOG")
    for key,lo,hi in [("BOOTSTRAP_N",50,20000),("SEED",0,2**32-1),("MIN_N",120,1000000),
                      ("MIN_CALCULATE_N",3,120),("QUANTILE_TYPE",6,7),("REED_ITERATIONS",1,10),("REED_BLOCK_MAX",1,3),
                      ("MAX_GROUPS",1,1000),("REPORT_MAX_ROWS",1,10000),("PLOT_MAX_GROUPS",0,100),
                      ("VERIFY_BATCH",1,2)]:
        cfg[key]=_integer(cfg[key],key,lo,hi)
    for key in ["COVERAGE","CI_LEVEL","TUKEY_K","DIXON_ALPHA","OUTLIER_WARN_RATE","CI_WIDTH_WARN_RATIO"]:
        cfg[key]=float(cfg[key])
        if not math.isfinite(cfg[key]):raise ValueError(key+" must be finite")
    engine.probabilities(cfg["COVERAGE"])
    if not 0<cfg["CI_LEVEL"]<1:raise ValueError("CI_LEVEL must be between 0 and 1")
    if not 0<=cfg["OUTLIER_WARN_RATE"]<=1:raise ValueError("OUTLIER_WARN_RATE must be between 0 and 1")
    if cfg["TUKEY_K"]<=0 or cfg["CI_WIDTH_WARN_RATIO"]<=0:raise ValueError("Fence/CI width thresholds must be positive")
    if cfg["DIXON_ALPHA"] not in {.01,.05,.1}:raise ValueError("DIXON_ALPHA must be .01, .05, .1")
    if cfg["BOOTSTRAP_TYPE"] not in {"PERCENTILE","BASIC"}:raise ValueError("Invalid BOOTSTRAP_TYPE")
    if cfg["CI_METHOD"]=="RANK" and cfg["METHOD"]!="NONPARAMETRIC":raise ValueError("RANK requires primary NONPARAMETRIC method")
    if cfg["CI_METHOD"]=="PARAMETRIC" and cfg["METHOD"] not in {"PARAMETRIC","LOG_PARAMETRIC"}:
        raise ValueError("PARAMETRIC CI requires primary PARAMETRIC or LOG_PARAMETRIC method")
    for key in ["SEX_MAP","POPULATION","VERIFY"]:
        if not isinstance(cfg[key],dict):raise ValueError(key+" must be a table")
    if not isinstance(cfg["INCLUDE"],list):raise ValueError("INCLUDE must be a list of conditions")
    if len(set(cfg["RESULT_IDS"]))!=len(cfg["RESULT_IDS"]) or len(set(cfg["PARTITION_BY"]))!=len(cfg["PARTITION_BY"]):
        raise ValueError("Result and partition identifiers must be unique")
    cfg["AGE_CUTS"]=[float(v) for v in cfg["AGE_CUTS"]]
    if "AGE" in cfg["PARTITION_BY"]:
        cuts=cfg["AGE_CUTS"]
        if len(cuts)<2 or not all(math.isfinite(v) and v>=0 for v in cuts) or any(a>=b for a,b in zip(cuts,cuts[1:])):
            raise ValueError("AGE partition needs explicit increasing AGE_CUTS in years")
    if cfg["VERIFY"] and cfg["OUTLIER_ACTION"]=="REMOVE":
        raise ValueError("Verification requires 20 outlier-reviewed individuals: exclude confirmed aberrant specimens and obtain replacements before verification; do not just remove rows")
    if cfg["FIRST_BATCH_OUTSIDE"] is not None:
        cfg["FIRST_BATCH_OUTSIDE"]=_integer(cfg["FIRST_BATCH_OUTSIDE"],"FIRST_BATCH_OUTSIDE",0,20)
    first_ids=[str(v) for v in cfg["FIRST_BATCH_SUBJECT_IDS"]]
    cfg["FIRST_BATCH_SUBJECT_IDS"]=first_ids
    if cfg["VERIFY"] and cfg["VERIFY_BATCH"]==2:
        if cfg["FIRST_BATCH_OUTSIDE"] not in {3,4} or len(first_ids)!=20 or len(set(first_ids))!=20 or any(not v.strip() for v in first_ids):
            raise ValueError("Second verification batch requires FIRST_BATCH_OUTSIDE=3 or 4 and 20 unique FIRST_BATCH_SUBJECT_IDS")
    if cfg["VERIFY_BATCH"]==1 and (first_ids or cfg["FIRST_BATCH_OUTSIDE"] is not None):
        raise ValueError("First-batch history is only valid for VERIFY_BATCH=2")
    confirmed=cfg["POPULATION"].get("REFERENCE_INDIVIDUALS_CONFIRMED",False)
    if type(confirmed) is not bool:raise ValueError("REFERENCE_INDIVIDUALS_CONFIRMED must be a boolean")
    if confirmed and any(not str(cfg["POPULATION"].get(k,"")).strip() for k in ["DESCRIPTION","SELECTION_CRITERIA","PREANALYTICAL","ANALYTICAL"]):
        raise ValueError("A confirmed reference population needs DESCRIPTION, SELECTION_CRITERIA, PREANALYTICAL and ANALYTICAL documentation")
    study_flow.validate(cfg)
    return cfg


def _single_role(dataset,tag,identifier=""):
    if identifier:return resolve_id(dataset,identifier)
    columns=dataset.columns_with_tag(tag)
    if len(columns)!=1:raise ValueError(f"Exactly one {tag} column or explicit COLUMN.ID is required")
    return columns[0]


def _partition_factors(dataset,cfg):
    normalized,_=normalize_categories(dataset)
    factors={}
    for factor in cfg["PARTITION_BY"]:
        if factor=="AGE":
            column=_single_role(dataset,"AGE",cfg["AGE_ID"])
            ages=age_series_for(dataset,column)
            cuts=cfg["AGE_CUTS"]
            top=ci_get(dataset.column_metadata(column),"TOP_CODE_VALUE",None)
            if top is not None and any(c>float(top) for c in cuts):
                raise ValueError("AGE_CUTS cannot distinguish values above the declared top-coded age")
            labels=[f"[{a:g},{b:g})" for a,b in zip(cuts,cuts[1:])]
            factors[factor]=pd.cut(ages,cuts,right=False,labels=labels).astype(object)
        elif factor=="SEX":
            column=_single_role(dataset,"SEX",cfg["SEX_ID"])
            raw=normalized.df[column.name]
            mapping=cfg["SEX_MAP"]
            factors[factor]=raw.map(lambda v: None if cell_state(v)!=STATE_VALUE else mapping.get(str(v),str(v)))
            # Exact codes are retained if no map is provided; semantics are never guessed.
        else:
            column=resolve_id(normalized,factor)
            factors[factor]=normalized.df[column.name].map(lambda v: str(v) if cell_state(v)==STATE_VALUE else None)
    return factors


def _values(dataset,column,cfg):
    metadata=dataset.column_metadata(column)
    policy=cfg["CENSOR_POLICY"]
    if ci_get(metadata,"CENSORING",None) is not None or ci_get(metadata,"CENSORING_INTERVAL",None) is not None:
        return measurement_values(dataset,column,"DELETE" if policy=="EXCLUDE" else "VALUE" if policy=="VALUE" else "RELEASED")
    values=[]; censored=[]
    for raw in dataset.df[column.name]:
        if cell_state(raw)!=STATE_VALUE:
            values.append(np.nan); censored.append(False); continue
        parsed=parse_comparator_number(str(raw).strip())
        if parsed is None or not math.isfinite(parsed[1]):
            raise ValueError(f"{column.name}: invalid quantitative result {raw!r}")
        flag=parsed[0] not in {"","="}
        if flag and policy=="RELEASED":
            raise ValueError("RELEASED censor policy requires an explicit source/flag binding")
        values.append(np.nan if flag and policy=="EXCLUDE" else parsed[1]); censored.append(flag)
    return pd.Series(values,index=dataset.df.index,dtype=float),pd.Series(censored,index=dataset.df.index,dtype=bool)


def _subject_column(dataset,cfg):
    identifier=cfg["SUBJECT_ID"] or ci_get(ci_get(dataset.meta,"OBSERVATION",{}),"SUBJECT_ID","")
    if identifier:return resolve_id(dataset,identifier)
    observation=ci_get(dataset.meta,"OBSERVATION",{})
    keys=ci_get(observation,"KEY_IDS",[])
    if ci_get(observation,"ROW_UNIT","")=="person" and len(keys)==1:return resolve_id(dataset,keys[0])
    return None


def _make_inputs(dataset,cfg):
    identifiers=column_ids(dataset)
    columns=[resolve_id(dataset,v) for v in cfg["RESULT_IDS"]] if cfg["RESULT_IDS"] else dataset.columns_with_tag("RESULT")
    if not columns:raise ValueError("RI_EP28 needs RESULT tags or explicit RESULT_IDS")
    for column in columns:
        if column.name not in {v.name for v in identifiers.values()}:
            raise ValueError(column.name+": declare COLUMN.ID to preserve result identity")
        require_quantitative(dataset,column)
    selected_ids={ci_get(dataset.column_metadata(c),"ID") for c in columns}
    if not set(cfg["VERIFY"])<=selected_ids:
        raise ValueError("VERIFY contains an unknown or unselected result COLUMN.ID")
    subject=_subject_column(dataset,cfg)
    factors=_partition_factors(dataset,cfg)
    included,condition_missing=(_conditions(dataset,cfg["INCLUDE"]) if cfg["INCLUDE"] else
                               (np.ones(len(dataset.df),dtype=bool),np.zeros(len(dataset.df),dtype=bool)))
    item=resolve_id(dataset,cfg["ITEM_ID"]) if cfg["ITEM_ID"] else None
    if item is None:
        tagged=dataset.columns_with_tag("TESTNAME") or dataset.columns_with_tag("ITEM")
        if len(tagged)>1:raise ValueError("Ambiguous test/item tag; supply ITEM_ID")
        item=tagged[0] if tagged else None
    audit=[]; frames=[]
    for column in columns:
        metadata=dataset.column_metadata(column)
        identifier=ci_get(metadata,"ID")
        values,censored=_values(dataset,column,cfg)
        eligible,eligibility_missing=_eligibility(dataset,column)
        status_ok,status_reason=result_status(dataset,column)
        raw=dataset.df[column.name]
        states=raw.map(cell_state)
        active=included & eligible
        if cfg["CENSOR_POLICY"]=="ERROR" and (active & censored.to_numpy()).any():
            raise ValueError(f"{identifier}: censored reference values require an explicit CENSOR_POLICY")
        names=dataset.df[item.name].map(lambda v: str(v) if cell_state(v)==STATE_VALUE else None) if item else pd.Series(identifier,index=raw.index)
        unit_id=ci_get(metadata,"UNIT_ID",None)
        unit=str(ci_get(metadata,"UNIT","")).strip()
        units=dataset.df[resolve_id(dataset,unit_id).name] if unit_id else pd.Series(unit,index=raw.index)
        eligible_frame=[]
        for pos in range(len(raw)):
            reason=("INCLUDE_CONTEXT_MISSING" if condition_missing[pos] else "OUTSIDE_COHORT" if not included[pos] else
                    str(status_reason.iloc[pos]) if not status_ok.iloc[pos] else
                    "ELIGIBILITY_CONTEXT_MISSING" if eligibility_missing[pos] else "NOT_ELIGIBLE" if not eligible[pos] else
                    "CENSORED_EXCLUDED" if censored.iloc[pos] and cfg["CENSOR_POLICY"]=="EXCLUDE" else
                    states.iloc[pos] if states.iloc[pos]!=STATE_VALUE else "NONFINITE" if not np.isfinite(values.iloc[pos]) else
                    "ITEM_MISSING" if names.iloc[pos] is None else "INCLUDED")
            row=dict(result_id=identifier,test_name=names.iloc[pos],source_row=pos+2,
                     raw_value=str(raw.iloc[pos]) if states.iloc[pos]==STATE_VALUE else "",
                     cell_state=states.iloc[pos],censored=bool(censored.iloc[pos]),reason=reason,
                     subject_id=str(dataset.df[subject.name].iloc[pos]) if subject is not None else "",
                     analysis_value=float(values.iloc[pos]) if np.isfinite(values.iloc[pos]) else None)
            for factor,series in factors.items():
                value=series.iloc[pos]
                row[factor]=None if pd.isna(value) else str(value)
            audit.append(row)
            if reason!="INCLUDED":continue
            if cell_state(units.iloc[pos])!=STATE_VALUE or not str(units.iloc[pos]).strip():
                raise ValueError(identifier+": missing result UNIT")
            if subject is not None and (cell_state(dataset.df[subject.name].iloc[pos])!=STATE_VALUE or not row["subject_id"].strip()):
                raise ValueError("Missing reference-individual identifier")
            record=dict(row,unit=str(units.iloc[pos]),position=pos)
            for factor,series in factors.items():
                value=series.iloc[pos]
                record[factor]=None if pd.isna(value) else str(value)
            eligible_frame.append(record)
        frame=pd.DataFrame(eligible_frame)
        if len(frame):
            if identifier in cfg["VERIFY"] and frame.test_name.nunique()>1:
                raise ValueError("Verification of long-format results needs one analyte per RESULT_ID; select the analyte explicitly with INCLUDE")
            for name,g in frame.groupby("test_name",sort=True):
                if g.unit.nunique()!=1:raise ValueError(f"{identifier}/{name}: mixed units must be harmonized before RI analysis")
                if subject is not None and g.subject_id.duplicated().any():
                    raise ValueError(f"{identifier}/{name}: repeated reference individuals; select one observation explicitly")
            frames.append(frame)
    return frames,pd.DataFrame(audit),subject is not None


def _group_frames(frame,cfg):
    yield "ALL",{},frame
    factors=cfg["PARTITION_BY"]
    combinations_to_use=[(f,) for f in factors]
    if len(factors)>1:combinations_to_use.append(tuple(factors))
    count=1
    for fields in combinations_to_use:
        present=frame.loc[frame[list(fields)].notna().all(axis=1)]
        grouper=fields[0] if len(fields)==1 else list(fields)
        for key,g in present.groupby(grouper,sort=True):
            keys=(key,) if len(fields)==1 else key
            definition=dict(zip(fields,keys))
            count+=1
            if count>cfg["MAX_GROUPS"]:raise ValueError("Partition count exceeds MAX_GROUPS; revise the prespecified partitions")
            yield json.dumps(definition,ensure_ascii=False,sort_keys=True),definition,g


def _outliers(values,cfg,method=None):
    return engine.detect_outliers(values,method or cfg["OUTLIER_METHOD"],tukey_k=cfg["TUKEY_K"],
        alpha=cfg["DIXON_ALPHA"],reed_iterations=cfg["REED_ITERATIONS"],reed_block_max=cfg["REED_BLOCK_MAX"])


def _interval_row(x,cfg,method,identity,*,original_n,outlier_n,censored_n,subject_known):
    row=dict(identity,method=method,primary=method==cfg["METHOD"],n_before_outliers=original_n,n=len(x),
             unique_individual_ids_confirmed=subject_known,censored_n=censored_n,
             outlier_method=cfg["OUTLIER_METHOD"],outlier_action=cfg["OUTLIER_ACTION"],outliers_flagged=outlier_n,
             outliers_removed=outlier_n if cfg["OUTLIER_ACTION"]=="REMOVE" else 0,
             coverage=cfg["COVERAGE"],ci_level=cfg["CI_LEVEL"],quantile_type=cfg["QUANTILE_TYPE"],
             ref_low=None,ref_high=None,low_ci_low=None,low_ci_high=None,high_ci_low=None,high_ci_high=None,
             ci_method="not_calculated",status="insufficient_n",notes="",details="{}")
    notes=[]
    if len(x)<cfg["MIN_CALCULATE_N"]:
        row["notes"]=f"Fewer than {cfg['MIN_CALCULATE_N']} retained values; collect additional reference individuals"
        return row
    if method=="NONPARAMETRIC" and len(x)+1 < 1/engine.probabilities(cfg["COVERAGE"])[0]-1e-10:
        row["status"]="insufficient_order_resolution"
        row["notes"]="Requested tail percentiles are beyond resolvable order ranks; select an appropriate coverage or another explicit method"
        return row
    try:
        e=engine.estimate_limits(x,method,cfg["COVERAGE"],cfg["QUANTILE_TYPE"],boxcox=cfg["BOXCOX"],normal_z=cfg["NORMAL_Z"])
        row.update(ref_low=e.low,ref_high=e.high,details=json.dumps(e.details,sort_keys=True))
        ci=engine.reference_ci(x,method,coverage=cfg["COVERAGE"],confidence=cfg["CI_LEVEL"],
            ci_method=cfg["CI_METHOD"] if method==cfg["METHOD"] else "AUTO",quantile_type=cfg["QUANTILE_TYPE"],
            repetitions=cfg["BOOTSTRAP_N"],seed=cfg["SEED"],bootstrap_type=cfg["BOOTSTRAP_TYPE"],
            bootstrap_quantile=cfg["BOOTSTRAP_QUANTILE"],boxcox=cfg["BOXCOX"],normal_z=cfg["NORMAL_Z"])
        row.update(low_ci_low=ci["bounds"][0][0],low_ci_high=ci["bounds"][0][1],
                   high_ci_low=ci["bounds"][1][0],high_ci_high=ci["bounds"][1][1],ci_method=ci["method"],
                   bootstrap_valid=ci["bootstrap_valid"],ci_details=json.dumps(ci,ensure_ascii=False))
        row['point_estimate']=cfg['POINT_ESTIMATE']
        if cfg['POINT_ESTIMATE']=='BOOTSTRAP_MEAN':
            direct=[e.low,e.high]
            e=engine.Estimate(*ci['bootstrap_mean'],method,dict(e.details,direct_limits=direct,point_estimate='mean_of_bootstrap_limits'))
            row.update(ref_low=e.low,ref_high=e.high,details=json.dumps(e.details,sort_keys=True))
        notes.extend(ci["warnings"])
        if cfg['BOOTSTRAP_N']<100 and ci['bootstrap_valid']:
            notes.append('Fewer than 100 bootstrap replicates: historical-study setting; Monte Carlo precision is limited')
        if e.details.get('origin_at_boundary') or e.details.get('lambda_at_boundary'):
            notes.append('Shifted Box-Cox fit reached a declared search boundary; review fitting-range sensitivity')
        row["status"]="candidate_requires_verification" if len(x)>=cfg["MIN_N"] else "small_sample_review"
        if len(x)<cfg["MIN_N"]:notes.append(f"n<{cfg['MIN_N']} per partition; method choice does not remove small-sample uncertainty")
        if not cfg["POPULATION"].get("REFERENCE_INDIVIDUALS_CONFIRMED",False):
            row["status"]="exploratory_only"
            notes.append("Reference-population suitability not confirmed; these are sample-based candidate limits")
        if not subject_known:
            row["status"]="exploratory_only"
            notes.append("n counts rows; independent individuals have not been established")
        if censored_n:
            row["status"]="censoring_sensitivity_only"
            notes.append("Censored observations were excluded or substituted; tails may be biased")
        width=e.high-e.low
        ratios=[(b-a)/width for a,b in ci["bounds"] if a is not None and b is not None and width>0]
        row["max_ci_width_ratio"]=max(ratios) if ratios else None
        if any(v is None for bound in ci["bounds"] for v in bound):
            notes.append("Reference-limit CI unavailable; do not treat precision as established")
            if row["status"]=="candidate_requires_verification":row["status"]="ci_review"
        if ratios and max(ratios)>cfg["CI_WIDTH_WARN_RATIO"]:
            notes.append("CI width exceeds the configured fraction of RI width (review threshold, not a universal CLSI rule)")
            if row["status"]=="candidate_requires_verification":row["status"]="ci_review"
        transformed=x
        if method in {"LOG_PARAMETRIC","LOG_ROBUST"}:transformed=np.log(x)
        elif method in {"BOXCOX_PARAMETRIC","BOXCOX_ROBUST"}:transformed=engine.special.boxcox(x,e.details["lambda"])
        elif method=="BOXCOX_SHIFTED_PARAMETRIC":
            transformed=engine.special.boxcox((x-e.details['origin'])/e.details['normalization_scale'],e.details['lambda'])
        norm=engine.normality(transformed)
        if 'KS_LILLIEFORS' in cfg['NORMALITY_TESTS'] and len(transformed)>=4:
            row.update(study_flow.normality_extras(transformed))
        row.update(shapiro_p=norm["shapiro_p"],skewness=norm["skewness"],normality_status=norm["status"])
        if method in {"PARAMETRIC","LOG_PARAMETRIC","BOXCOX_PARAMETRIC","BOXCOX_SHIFTED_PARAMETRIC"} and norm["status"] in {"review_non_normal","constant"}:
            notes.append("Distribution on the fitted scale needs review; inspect histogram and Q-Q plot")
            if row["status"]=="candidate_requires_verification":row["status"]="distribution_review"
        if "ROBUST" in method and norm["skewness"] is not None and abs(norm["skewness"])>1:
            notes.append("Basic Appendix B robust estimator needs symmetry review; consider a prespecified transformation for skewed data")
            if row["status"]=="candidate_requires_verification":row["status"]="distribution_review"
        if method=="HARRELL_DAVIS" and len(x)<100:
            notes.append("Harrell-Davis with n<100 needs particular caution; weighted tail estimate is sensitive to outliers")
        if e.low<0 and np.min(x)>=0:
            notes.append("Negative lower limit from a nonnegative sample: check physical plausibility and the fitted distribution; no automatic zero clamp")
            if row["status"]=="candidate_requires_verification":row["status"]="distribution_review"
        if width<=0:
            row["status"]="degenerate_distribution"
            notes.append("Zero-width interval does not establish a usable reference interval")
        if original_n and outlier_n/original_n>cfg["OUTLIER_WARN_RATE"]:
            notes.append("Flagged outlier fraction exceeds OUTLIER_WARN_RATE; review exclusions and sensitivity")
    except (ValueError,FloatingPointError,OverflowError) as exc:
        row["status"]="not_evaluable"
        notes.append(str(exc))
    row["notes"]="; ".join(notes)
    return row


def _partition_tables(frame,cfg,identity):
    rows=[]; tails=[]
    factors=cfg["PARTITION_BY"]
    for factor in factors:
        other=[f for f in factors if f!=factor]
        strata=[("ALL",frame)]
        if other:
            complete=frame.loc[frame[other].notna().all(axis=1)]
            for key,g in complete.groupby(other[0] if len(other)==1 else other,sort=True):
                keys=(key,) if len(other)==1 else key
                strata.append((json.dumps(dict(zip(other,keys)),ensure_ascii=False,sort_keys=True),g))
        for stratum,data in strata:
            data=data.loc[data[factor].notna()]
            groups=list(data.groupby(factor,sort=True))
            if len(groups)<2:continue
            # Use each subgroup's declared outlier policy consistently for both
            # pairwise diagnostics and the pooled interval being assessed.
            clean={}; errors={}
            for label,g in groups:
                x=g.analysis_value.to_numpy(dtype=float)
                try:
                    if cfg["PARAMETRIC_TRIM_Z"] is not None:
                        trim,_=study.gaussian_trim(x,cfg["METHOD"],cfg["PARAMETRIC_TRIM_Z"],cfg["BOXCOX"])
                        x=x[~trim]
                    out=_outliers(x,cfg)
                    clean[label]=x[~out.mask] if cfg["OUTLIER_ACTION"]=="REMOVE" else x
                except ValueError as exc:errors[label]=str(exc)
            for (a,ga),(b,gb) in combinations(groups,2):
                row=dict(identity,factor=factor,stratum=stratum,group_a=a,group_b=b,
                         n_a=len(clean.get(a,[])),n_b=len(clean.get(b,[])),
                         decision="not_evaluable",notes="Prespecified subgroup comparison; no automatic partition adoption")
                try:
                    if a in errors or b in errors:raise ValueError(errors.get(a) or errors[b])
                    row.update(engine.harris_boyd(clean[a],clean[b],scale=cfg["PARTITION_SCALE"]))
                    if 'WILCOXON' in cfg['PARTITION_DIAGNOSTICS']:
                        row.update(study.wilcoxon_partition(clean[a],clean[b]))
                    if 'SDR_BR' in cfg['PARTITION_DIAGNOSTICS']:
                        vals=np.concatenate([clean[a],clean[b]])
                        scaled=np.log(vals) if cfg['PARTITION_SCALE']=='LOG' else vals
                        row.update(study.variance_sdr(scaled,[str(a)]*len(clean[a])+[str(b)]*len(clean[b])))
                        row.update(study.bias_ratios(study_flow.point(clean[a],cfg),study_flow.point(clean[b],cfg),
                            study_flow.point(vals,cfg),cfg['REPORTING_UNITS'].get(identity['result_id'])))
                        row['sdr_threshold']=.4
                        row['sdr_br_signal']=bool(row['sdr']>=.4 or max(row['br_low'],row['br_high'])>.375)
                        row['partition_adoption']='review_only; assess 3*reporting-unit differences and clinical context' 
                except ValueError as exc:row["notes"]=str(exc)
                rows.append(row)
            if errors or any(len(v)<2 for v in clean.values()):continue
            try:
                pooled=np.concatenate(list(clean.values()))
                e=engine.Estimate(*study_flow.point(pooled,cfg),cfg["METHOD"])
                for label,x in clean.items():
                    for row in engine.tail_proportions(x,e.low,e.high,cfg["CI_LEVEL"]):
                        # Lahti 4.1/3.2 thresholds apply only to central 95%.
                        if not math.isclose(cfg["COVERAGE"],.95):row["signal"]="threshold_not_applicable_to_coverage"
                        tails.append(dict(identity,factor=factor,stratum=stratum,group=label,
                            pooled_low=e.low,pooled_high=e.high,pooled_n=len(pooled),**row))
            except ValueError as exc:
                tails.append(dict(identity,factor=factor,stratum=stratum,group="ALL",signal="not_evaluable",notes=str(exc)))
    return rows,tails


def _verification(g,cfg,identity,subject_known,detected,censored_n):
    spec=cfg["VERIFY"].get(identity["result_id"])
    if spec is None:return None
    if not isinstance(spec,dict) or set(spec)!={"LOW","HIGH","UNIT","REFERENCE"}:
        raise ValueError("VERIFY entries require exactly LOW, HIGH, UNIT and REFERENCE")
    if spec["UNIT"]!=identity["unit"] or not str(spec["REFERENCE"]).strip():
        raise ValueError("Verification requires matching UNIT and an external reference citation")
    row=dict(identity,status="not_evaluable",reference=str(spec["REFERENCE"]))
    if not subject_known or not cfg["POPULATION"].get("REFERENCE_INDIVIDUALS_CONFIRMED",False):
        row["notes"]="20-person verification requires identified, qualified reference individuals"
        return row
    if censored_n:
        row["notes"]="Censored values require review; do not substitute concentrations in 20-person verification"
        return row
    if cfg["VERIFY_BATCH"]==2 and set(g.subject_id)&set(cfg["FIRST_BATCH_SUBJECT_IDS"]):
        row["notes"]="Second verification batch contains individuals from the first batch; obtain 20 new independent individuals"
        return row
    if cfg["OUTLIER_METHOD"]=="NONE":
        row["notes"]="EP28 section 11.2 requires outlier review; select REED or TUKEY screening"
        return row
    if detected.mask.any():
        row["status"]="outlier_review_and_replacement_required"
        row["outliers_flagged"]=int(detected.mask.sum())
        row["notes"]="Review flagged specimens; replace confirmed aberrant observations to obtain 20 acceptable values before verification"
        return row
    try:
        row.update(engine.verify_twenty(g.analysis_value.to_numpy(dtype=float),float(spec["LOW"]),float(spec["HIGH"]),
                    batch=cfg["VERIFY_BATCH"],first_batch_outside=cfg["FIRST_BATCH_OUTSIDE"]))
        row["status"]="evaluated"
        row["notes"]="Statistical verification criterion; analytical comparability and clinical approval still required"
    except ValueError as exc:row["notes"]=str(exc)
    return row


@register_plugin("REFERENCE_INTERVAL_EP28",description="EP28 reference-interval methods, outlier audit, partitions, verification and Korean reports.",roles=(RESULT_ROLE,))
@register_plugin("RI_EP28",description="Alias of REFERENCE_INTERVAL_EP28.",roles=(RESULT_ROLE,))
@operation_boundary("REFERENCE_INTERVAL_EP28")
def reference_interval_ep28(dataset,meta,step_name,options):
    cfg=_config(dataset,options)
    record_effective_parameters(cfg)
    dataset=dataset.replace(df=dataset.df.reset_index(drop=True).copy())
    observation=observation_info(dataset)
    input_cfg=deepcopy(cfg)
    selected=cfg['RESULT_IDS'] or [ci_get(dataset.column_metadata(c),'ID') for c in dataset.columns_with_tag('RESULT')]
    if cfg['LAVE']:
        if not set(cfg['LAVE']['TARGET_IDS'])<=set(selected):raise ValueError('LAVE TARGET_IDS must be selected RESULT_IDS')
        input_cfg['RESULT_IDS']=list(dict.fromkeys(selected+cfg['LAVE']['REFERENCE_IDS']))
    frames,audit,subject_known=_make_inputs(dataset,input_cfg)
    lave_selection,lave_audit,lave_history,lave_warnings=study_flow.apply_lave(frames,cfg,subject_known)
    frames=[f for f in frames if f.result_id.iloc[0] in selected]
    results=[]; outlier_rows=[]; sensitivity=[]; partitions=[]; tails=[]; verifications=[]; membership=[]
    plot_groups=[]; trim_audit=[]; nested_rows=[]
    warnings=["Reference intervals describe a defined reference population; they are not diagnostic decision limits.",
              "Partition tests and normality p-values support review, not automatic clinical decisions."]
    if ci_get(dataset.meta,"SURVEY",None):warnings.append("Survey design is not used by this direct RI estimator; results are unweighted sample candidates")
    if cfg['LAVE']:
        warnings.append("LAVE is a declared cross-analyte secondary exclusion; inspect subject membership and iteration tables before interpreting limits")
    if cfg['PARAMETRIC_TRIM_Z'] is not None:
        warnings.append("Parametric peripheral-value removal is a declared one-pass study procedure; inspect its separate audit table")
    if cfg['POINT_ESTIMATE']=='BOOTSTRAP_MEAN':
        warnings.append("Point estimates are means of resampled reference limits rather than direct full-sample limits")
    warnings.extend(lave_warnings)
    for frame in frames:
        for (identifier,name,unit),test in frame.groupby(["result_id","test_name","unit"],sort=True):
            identity=dict(result_id=identifier,test_name=name,unit=unit)
            before_lave=test.copy()
            if cfg['LAVE'] and identifier in cfg['LAVE']['TARGET_IDS']:
                test=test.loc[[lave_selection.get((identifier,v),False) for v in test.subject_id]].copy()
            if 'NESTED_SDR' in cfg['PARTITION_DIAGNOSTICS'] and len(cfg['PARTITION_BY'])==2:
                factors=cfg['PARTITION_BY'];valid=test.dropna(subset=factors)
                entry=dict(identity,outer=factors[0],inner=factors[1],status='not_evaluable',selection='after LAVE, before univariate trimming/outliers')
                try:
                    values=valid.analysis_value.to_numpy(float)
                    if cfg['PARTITION_SCALE']=='LOG':
                        if np.any(values<=0):raise ValueError('Nested log SDR requires positive values')
                        values=np.log(values)
                    entry.update(study.variance_sdr(values,valid[factors[0]],valid[factors[1]]),status='evaluated')
                except ValueError as exc:entry['notes']=str(exc)
                nested_rows.append(entry)
            p,t=_partition_tables(test,cfg,identity)
            partitions.extend(p); tails.extend(t)
            for label,definition,g in _group_frames(test,cfg):
                group_identity=dict(identity,group=label,partition_definition=json.dumps(definition,ensure_ascii=False,sort_keys=True))
                x=g.analysis_value.to_numpy(dtype=float)
                before_trim=g.copy();trim_count=0
                try:
                    if cfg['PARAMETRIC_TRIM_Z'] is not None:
                        trim,trim_details=study.gaussian_trim(x,cfg['METHOD'],cfg['PARAMETRIC_TRIM_Z'],cfg['BOXCOX'])
                        for record,flag in zip(g.to_dict('records'),trim):
                            trim_audit.append(dict(group_identity,source_row=record['source_row'],subject_id=record['subject_id'],
                                removed=bool(flag),value=record['analysis_value'],criteria=json.dumps(trim_details,sort_keys=True)))
                        trim_count=int(trim.sum());g=g.loc[~trim].copy();x=x[~trim]
                    detected=_outliers(x,cfg) if len(x)>=2 else engine.OutlierResult(np.zeros(len(x),dtype=bool),cfg["OUTLIER_METHOD"])
                except ValueError as exc:
                    for method in cfg["METHODS"]:
                        results.append(dict(group_identity,method=method,primary=method==cfg["METHOD"],n=len(x),
                                            status="outlier_method_not_evaluable",notes=str(exc)))
                    warnings.append(f"{identifier}/{label}: {exc}")
                    continue
                warnings.extend(f"{identifier}/{label}: {note}" for note in detected.warnings)
                keep=~detected.mask if cfg["OUTLIER_ACTION"]=="REMOVE" else np.ones(len(x),dtype=bool)
                retained=x[keep]
                for position,(record,flag,used) in enumerate(zip(g.to_dict("records"),detected.mask,keep)):
                    membership.append(dict(group_identity,source_row=record["source_row"],subject_id=record["subject_id"],
                                           outlier_flagged=bool(flag),included_in_interval=bool(used)))
                    if flag:
                        outlier_rows.append(dict(group_identity,source_row=record["source_row"],subject_id=record["subject_id"],
                            raw_value=record["raw_value"],value=record["analysis_value"],method=cfg["OUTLIER_METHOD"],
                            action=cfg["OUTLIER_ACTION"],criteria=json.dumps(detected.details,ensure_ascii=False,sort_keys=True)))
                missing_partitions=int(test[cfg["PARTITION_BY"]].isna().any(axis=1).sum()) if cfg["PARTITION_BY"] else 0
                source_group=audit.loc[audit.result_id.eq(identifier) & audit.test_name.eq(name) & audit.reason.isin(["INCLUDED","CENSORED_EXCLUDED"])]
                for factor,value in definition.items():source_group=source_group.loc[source_group[factor].eq(value)]
                censored_n=int(source_group.censored.sum())
                for method in cfg["METHODS"]:
                    row=_interval_row(retained,cfg,method,group_identity,original_n=len(x),outlier_n=int(detected.mask.sum()),
                                      censored_n=censored_n,subject_known=subject_known)
                    original=before_lave
                    for factor,value in definition.items():original=original.loc[original[factor].eq(value)]
                    row.update(n_before_lave=len(original),lave_removed=len(original)-len(before_trim),
                               n_before_parametric_trim=len(before_trim),parametric_trim_removed=trim_count)
                    if lave_warnings and identifier in cfg['LAVE'].get('TARGET_IDS',[]):
                        row['status']='lave_iteration_review';row['notes']+='; '+'; '.join(lave_warnings)
                    row["partition_context_missing_n"]=missing_partitions
                    results.append(row)
                if len(plot_groups)<cfg["PLOT_MAX_GROUPS"] and len(retained)>=2:
                    plot_groups.append(dict(group_identity,values=retained,raw_values=x))
                verified=_verification(g,cfg,group_identity,subject_known,detected,censored_n)
                if verified is not None:verifications.append(verified)
                for out_method in list(dict.fromkeys(["NONE",cfg["OUTLIER_METHOD"],*cfg["OUTLIER_METHODS"]])) if cfg["OUTLIER_METHODS"] else []:
                    record=dict(group_identity,outlier_method=out_method,action="REMOVE_SENSITIVITY",method=cfg["METHOD"],
                                n_before=len(x),n=None,removed_n=None,ref_low=None,ref_high=None,status="not_evaluable",notes="")
                    try:
                        d=_outliers(x,cfg,out_method)
                        v=x[~d.mask]
                        record.update(n=len(v),removed_n=int(d.mask.sum()))
                        if len(v)<cfg["MIN_CALCULATE_N"]:raise ValueError("Insufficient retained sample for sensitivity RI")
                        if cfg["METHOD"]=="NONPARAMETRIC" and len(v)+1<1/engine.probabilities(cfg["COVERAGE"])[0]-1e-10:
                            raise ValueError("Insufficient order-rank resolution for nonparametric sensitivity limits")
                        e=engine.Estimate(*study_flow.point(v,cfg),cfg["METHOD"])
                        record.update(ref_low=e.low,ref_high=e.high,status="sensitivity_only",notes="; ".join(d.warnings))
                    except ValueError as exc:record["notes"]=str(exc)
                    sensitivity.append(record)
    main=pd.DataFrame(results)
    if main.empty:
        main=pd.DataFrame(columns=["result_id","test_name","unit","group","method","primary","n","status","ref_low","ref_high","notes"])
        warnings.append("No eligible quantitative reference observations")
    data_description={"headers":dataset.tagged_headers(),"rows":[[cell_state(v),str(v)] for row in dataset.df.itertuples(index=False,name=None) for v in row]}
    source_hash=sha256(json.dumps(data_description,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
    snapshot=dict(settings=cfg,metadata=dataset.meta,source_data_sha256=source_hash,observation=observation)
    audit_json=json.dumps(snapshot,ensure_ascii=False,sort_keys=True,default=str)
    effective_hash=sha256(audit_json.encode()).hexdigest()
    tables={"lave_membership":lave_audit,"lave_iterations":lave_history,"parametric_trim":pd.DataFrame(trim_audit),
            "nested_sdr":pd.DataFrame(nested_rows),"reference_intervals":main,"outliers":pd.DataFrame(outlier_rows),"outlier_sensitivity":pd.DataFrame(sensitivity),
            "partition_tests":pd.DataFrame(partitions),"partition_tails":pd.DataFrame(tails),"verification":pd.DataFrame(verifications),
            "input_audit":audit,"group_membership":pd.DataFrame(membership),
            "input_summary":audit.groupby(["result_id","reason"],dropna=False).size().rename("n").reset_index() if len(audit) else pd.DataFrame()}
    meta_out={"COLUMN":{c:{"ID":c} for c in main.columns},"RI_EP28_RESULT":{
        "VERSION":2,"SOURCE_DATA_SHA256":source_hash,"EFFECTIVE_INPUT_SHA256":effective_hash,
        "SETTINGS_JSON":json.dumps(cfg,ensure_ascii=False,sort_keys=True),"WARNINGS":warnings,
        "TEST_LABELS":{str(ci_get(dataset.column_metadata(c), "ID", c.name)):str(ci_get(dataset.column_metadata(c), "LABEL", c.name))
                       for c in dataset.columns_with_tag("RESULT")}}}
    specs=[ColumnSpec(c,c,["NUM","NULLABLE"] if pd.api.types.is_numeric_dtype(main[c]) and not pd.api.types.is_bool_dtype(main[c]) else ["STR","NULLABLE"]) for c in main.columns]
    result_dataset=TameDataset(main.copy(),specs,meta_out)
    charts=[]
    if len(main) and "ref_low" in main:
        primary=main.loc[main.primary.fillna(False) & main.ref_low.notna()]
        for (identifier, test_name, unit),g in primary.groupby(["result_id", "test_name", "unit"],sort=True):
            label = meta_out['RI_EP28_RESULT']['TEST_LABELS'].get(identifier, test_name) if test_name == identifier else test_name
            chart = chart_spec("EP28_LIMITS_"+str(len(charts)+1), type="interval",
                title=f"{label}: reference limits ({str(unit).replace('umol/L', 'µmol/L')})",
                x="group", y="ref_high", rows=g.to_dict("records"), max_points=len(g))
            chart.update(Y_LOW="ref_low", Y_HIGH="ref_high", LOW_CI_LOW="low_ci_low", LOW_CI_HIGH="low_ci_high",
                HIGH_CI_LOW="high_ci_low", HIGH_CI_HIGH="high_ci_high",
                FILTER={"result_id":identifier, "test_name":test_name, "unit":unit, "primary":True})
            charts.append(chart)
    result_dataset=with_visualizations(result_dataset,charts)
    result_dataset=append_log_entry(result_dataset,action="REFERENCE_INTERVAL_EP28",parent=dataset.source_path or "",
        parameters={"EFFECTIVE_INPUT_SHA256":effective_hash,"SOURCE_DATA_SHA256":source_hash},warnings=warnings,
        input_dataset=dataset, effective_parameters=cfg,
        counts={"INPUT_ROWS":len(dataset.df),"RESULT_ROWS":len(main),"OUTLIERS_FLAGGED":len(outlier_rows),
                "LAVE_EXCLUSION_RECORDS":int((~lave_audit.included).sum()) if len(lave_audit) else 0,
                "PARAMETRIC_TRIM_EXCLUSION_RECORDS":sum(bool(row['removed']) for row in trim_audit)},
        message=f"EP28 참고구간 계산: 입력 {len(dataset.df)}행, 결과 {len(main)}행")
    output=OperationOutput(name=step_name,dataset=result_dataset,table=main,tables=tables,charts=charts,warnings=warnings,
        message=f"EP28 candidate intervals: {len(main)} rows; clinical adoption requires review",files=[])
    if cfg["OUTPUT_DIR"] or cfg["REPORT_PATH"]:
        from tametools.ri_ep28_report import write_reports
        output.files=write_reports(output,cfg,snapshot,plot_groups)
    return output
