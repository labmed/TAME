"""RI_EP28 study-option validation and audit-table adapters."""
from __future__ import annotations
import json, math
import numpy as np
import pandas as pd
from scipy import stats
from . import ri_ep28 as engine
from . import ri_study as study


def validate(cfg):
    if cfg['POINT_ESTIMATE'] not in {'DIRECT','BOOTSTRAP_MEAN'}:raise ValueError('POINT_ESTIMATE must be DIRECT or BOOTSTRAP_MEAN')
    if cfg['BOOTSTRAP_QUANTILE'] not in {'TYPE7','R_BOOT'}:raise ValueError('BOOTSTRAP_QUANTILE must be TYPE7 or R_BOOT')
    if cfg['POINT_ESTIMATE']=='BOOTSTRAP_MEAN' and (cfg['CI_METHOD']!='BOOTSTRAP' or cfg['BOOTSTRAP_TYPE']!='PERCENTILE' or len(cfg['METHODS'])!=1):
        raise ValueError('BOOTSTRAP_MEAN requires one method, CI_METHOD=BOOTSTRAP and BOOTSTRAP_TYPE=PERCENTILE')
    if not isinstance(cfg['BOXCOX'],dict):raise ValueError('BOXCOX must be a table')
    if set(cfg['BOXCOX'])-{'ORIGIN','ORIGIN_GAP_BOUNDS','LAMBDA_BOUNDS'}:raise ValueError('Unknown BOXCOX option')
    for key in ['ORIGIN_GAP_BOUNDS','LAMBDA_BOUNDS']:
        if key in cfg['BOXCOX']:
            v=np.asarray(cfg['BOXCOX'][key],dtype=float)
            if v.shape!=(2,) or not np.isfinite(v).all() or v[0]>=v[1] or (key=='ORIGIN_GAP_BOUNDS' and v[0]<=0):
                raise ValueError('Invalid BOXCOX '+key)
    origin=cfg['BOXCOX'].get('ORIGIN','FIT')
    if str(origin).upper()!='FIT' and not math.isfinite(float(origin)):raise ValueError('Invalid BOXCOX ORIGIN')
    if cfg['NORMAL_Z'] is not None:
        cfg['NORMAL_Z']=float(cfg['NORMAL_Z'])
        if not math.isfinite(cfg['NORMAL_Z']) or cfg['NORMAL_Z']<=0 or cfg['COVERAGE']!=.95:
            raise ValueError('NORMAL_Z requires COVERAGE=.95 and a positive finite value')
        if any(m not in {'PARAMETRIC','LOG_PARAMETRIC','BOXCOX_PARAMETRIC','BOXCOX_SHIFTED_PARAMETRIC'} for m in cfg['METHODS']):
            raise ValueError('NORMAL_Z is only defined for parametric methods')
    if cfg['PARAMETRIC_TRIM_Z'] is not None:
        cfg['PARAMETRIC_TRIM_Z']=float(cfg['PARAMETRIC_TRIM_Z'])
        if not math.isfinite(cfg['PARAMETRIC_TRIM_Z']) or cfg['PARAMETRIC_TRIM_Z']<=0:raise ValueError('PARAMETRIC_TRIM_Z must be positive')
        if cfg['METHOD'] not in {'PARAMETRIC','LOG_PARAMETRIC','BOXCOX_PARAMETRIC','BOXCOX_SHIFTED_PARAMETRIC'}:
            raise ValueError('PARAMETRIC_TRIM_Z requires a parametric primary method')
    if not set(cfg['PARTITION_DIAGNOSTICS'])<={'WILCOXON','SDR_BR','NESTED_SDR'}:raise ValueError('Unknown PARTITION_DIAGNOSTICS')
    if not set(cfg['NORMALITY_TESTS'])<={'SHAPIRO','KS_LILLIEFORS'}:raise ValueError('Unknown NORMALITY_TESTS')
    if not isinstance(cfg['REPORTING_UNITS'],dict):raise ValueError('REPORTING_UNITS must map result IDs to positive units')
    for v in cfg['REPORTING_UNITS'].values():
        if not math.isfinite(float(v)) or float(v)<=0:raise ValueError('REPORTING_UNITS values must be positive')
    spec=cfg['LAVE']
    if not isinstance(spec,dict):raise ValueError('LAVE must be a table')
    if not spec:return
    defaults=dict(REFERENCE_IDS=[],TARGET_IDS=[],STRATIFY_BY=[],METHOD='BOXCOX_PARAMETRIC',
                  MAX_ABNORMAL=1,EXPANSION=.05,ITERATIONS=6,MIN_N=40,MISSING_POLICY='ERROR')
    if set(spec)-set(defaults):raise ValueError('Unknown LAVE option: '+','.join(sorted(set(spec)-set(defaults))))
    defaults.update(spec);cfg['LAVE']=spec=defaults
    for key in ['REFERENCE_IDS','TARGET_IDS','STRATIFY_BY']:
        if not isinstance(spec[key],list) or len(set(spec[key]))!=len(spec[key]):raise ValueError('LAVE '+key+' requires unique identifiers')
    if len(spec['REFERENCE_IDS'])<2 or not spec['TARGET_IDS']:raise ValueError('LAVE requires REFERENCE_IDS and explicit TARGET_IDS')
    if not set(spec['STRATIFY_BY'])<=set(cfg['PARTITION_BY']):raise ValueError('LAVE STRATIFY_BY must be declared in PARTITION_BY')
    if spec['METHOD'] not in engine.METHODS:raise ValueError('Unknown LAVE METHOD')
    if spec['MISSING_POLICY'] not in {'ERROR','EXCLUDE'}:raise ValueError('LAVE MISSING_POLICY must be ERROR or EXCLUDE')
    for key,low,high in [('MAX_ABNORMAL',0,len(spec['REFERENCE_IDS'])-2),('ITERATIONS',1,100),('MIN_N',3,1000000)]:
        v=spec[key]
        if isinstance(v,bool) or not isinstance(v,int) or not low<=v<=high:raise ValueError('Invalid LAVE '+key)
    if not math.isfinite(float(spec['EXPANSION'])) or spec['EXPANSION']<0:raise ValueError('Invalid LAVE EXPANSION')
    if cfg['VERIFY']:raise ValueError('LAVE is not applied to 20-person verification; select qualified replacements first')


def apply_lave(frames,cfg,subject_known):
    """Return membership keyed by result ID and subject, without altering source."""
    spec=cfg['LAVE']
    if not spec:return {},pd.DataFrame(),pd.DataFrame(),[]
    if not subject_known:raise ValueError('LAVE requires explicit unique subject identifiers')
    all_data=pd.concat(frames,ignore_index=True)
    needed=list(dict.fromkeys(spec['REFERENCE_IDS']+spec['TARGET_IDS']))
    available=set(all_data.result_id)
    if set(needed)-available:raise ValueError('LAVE has missing or ineligible tests: '+','.join(sorted(set(needed)-available)))
    panel=all_data.loc[all_data.result_id.isin(needed)].copy()
    if panel.groupby('result_id').test_name.nunique().gt(1).any():raise ValueError('LAVE requires one analyte per result ID; pivot long data first')
    if panel.duplicated(['subject_id','result_id']).any():raise ValueError('LAVE found repeated subject/analyte observations')
    pivot=panel.pivot(index='subject_id',columns='result_id',values='analysis_value')
    factors=spec['STRATIFY_BY'];strata=pd.DataFrame(index=pivot.index)
    for factor in factors:
        count=panel.groupby('subject_id')[factor].nunique(dropna=False)
        if count.gt(1).any():raise ValueError('LAVE inconsistent partition context for subject: '+factor)
        strata[factor]=panel.groupby('subject_id')[factor].first().reindex(pivot.index)
    if factors and strata.isna().any(axis=None):raise ValueError('LAVE stratification context is missing')
    subsets=[('ALL',pivot.index)] if not factors else [
        (json.dumps(dict(zip(factors,(key,) if len(factors)==1 else key)),sort_keys=True),group.index)
        for key,group in strata.groupby(factors[0] if len(factors)==1 else factors,sort=True)]
    selection={};audit=[];history=[];warnings=[]
    for stratum,ids in subsets:
        result=study.lave(pivot.loc[ids,spec['REFERENCE_IDS']].to_numpy(float),spec['REFERENCE_IDS'],
            targets=spec['TARGET_IDS'],method=spec['METHOD'],coverage=cfg['COVERAGE'],quantile_type=cfg['QUANTILE_TYPE'],
            max_abnormal=spec['MAX_ABNORMAL'],expansion=spec['EXPANSION'],iterations=spec['ITERATIONS'],
            min_n=spec['MIN_N'],missing_policy=spec['MISSING_POLICY'],boxcox=cfg['BOXCOX'],normal_z=cfg['NORMAL_Z'])
        if not result['converged']:warnings.append('LAVE '+stratum+': membership did not converge within the declared iteration limit; exploratory only')
        for row in result['history']:
            history.append(dict(row,stratum=stratum,details=json.dumps(row['details'],sort_keys=True)))
        for target in spec['TARGET_IDS']:
            for i,subject in enumerate(ids):
                keep=bool(result['masks'][target][i]);selection[target,subject]=keep
                audit.append(dict(result_id=target,subject_id=subject,stratum=stratum,included=keep,
                    abnormal_other_tests=int(result['abnormal_counts'][target][i]),panel_complete=bool(result['complete'][i]),
                    reason='LAVE_PANEL_MISSING' if not result['complete'][i] else 'LAVE_EXCLUDED' if not keep else 'LAVE_RETAINED',
                    converged=result['converged'],updates=result['updates'],target_self_screened=False))
    return selection,pd.DataFrame(audit),pd.DataFrame(history),warnings


def point(values,cfg):
    e=engine.estimate_limits(values,cfg['METHOD'],cfg['COVERAGE'],cfg['QUANTILE_TYPE'],boxcox=cfg['BOXCOX'],normal_z=cfg['NORMAL_Z'])
    if cfg['POINT_ESTIMATE']=='BOOTSTRAP_MEAN':
        ci=engine.reference_ci(values,cfg['METHOD'],coverage=cfg['COVERAGE'],confidence=cfg['CI_LEVEL'],ci_method='BOOTSTRAP',
            quantile_type=cfg['QUANTILE_TYPE'],repetitions=cfg['BOOTSTRAP_N'],seed=cfg['SEED'],
            bootstrap_type=cfg['BOOTSTRAP_TYPE'],bootstrap_quantile=cfg['BOOTSTRAP_QUANTILE'],boxcox=cfg['BOXCOX'],normal_z=cfg['NORMAL_Z'])
        return ci['bootstrap_mean']
    return [e.low,e.high]


def normality_extras(values):
    x=engine.finite_sample(values,4)
    if np.ptp(x)<=0:return dict(ks_status='constant',ks_d=None,ks_lilliefors_p=None)
    try:from statsmodels.stats.diagnostic import lilliefors
    except ImportError:return dict(ks_status='statsmodels_required',ks_d=None,ks_lilliefors_p=None)
    d,p=lilliefors(x,dist='norm',pvalmethod='table')
    return dict(ks_d=float(d),ks_lilliefors_p=float(p),ks_status='Lilliefors_fitted_normal_table; not proof of normality')
