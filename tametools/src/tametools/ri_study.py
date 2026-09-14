"""Declared public-study RI extensions (Omuse 2020; Ichihara 2017).

These are inspectable numerical procedures, not a claim to reproduce unpublished
software settings. LAVE excludes a target's own result from its screening panel.
"""
from __future__ import annotations
import math
import numpy as np
from scipy import optimize, special, stats


def shifted_boxcox(values, options=None):
    """Bounded profile maximum likelihood for power and origin, x-origin > 0.

    Origin search bounds are gaps below min(x), expressed as fractions of range.
    A fixed ORIGIN can instead be declared in original measurement units.
    Search bounds avoid the unbounded likelihood as the origin approaches min(x).
    """
    from .ri_ep28 import finite_sample
    x=finite_sample(values,3)
    if np.ptp(x)<=0:raise ValueError('Shifted Box-Cox requires nonconstant values')
    cfg={'ORIGIN':'FIT','ORIGIN_GAP_BOUNDS':[.01,10.],'LAMBDA_BOUNDS':[-5.,5.]}
    options=options or {}
    if not isinstance(options,dict) or set(options)-set(cfg):raise ValueError('Unknown BOXCOX option')
    cfg.update(options)
    bounds=np.asarray(cfg['LAMBDA_BOUNDS'],dtype=float)
    if bounds.shape!=(2,) or not np.isfinite(bounds).all() or bounds[0]>=bounds[1]:
        raise ValueError('LAMBDA_BOUNDS must be two increasing finite values')
    span=float(np.ptp(x));minimum=float(x.min())
    # Scale first: the likelihood and limits are invariant to measurement units.
    def profile(origin):
        z=(x-origin)/span
        if np.any(z<=0):return None
        logz=np.log(z)
        def objective(lam):
            with np.errstate(over='ignore',invalid='ignore'):
                y=special.boxcox(z,lam);var=float(np.var(y))
            return len(x)/2*math.log(var)-(lam-1)*float(logz.sum()) if var>0 and math.isfinite(var) else 1e100
        fit=optimize.minimize_scalar(objective,bounds=bounds,method='bounded',options={'xatol':1e-10})
        return float(fit.fun),float(fit.x)
    hit=False
    if str(cfg['ORIGIN']).upper()=='FIT':
        gaps=np.asarray(cfg['ORIGIN_GAP_BOUNDS'],dtype=float)
        if gaps.shape!=(2,) or not np.isfinite(gaps).all() or not 0<gaps[0]<gaps[1]:
            raise ValueError('ORIGIN_GAP_BOUNDS requires two increasing positive range fractions')
        lo,hi=np.log(gaps)
        def objective(loggap):return profile(minimum-span*math.exp(loggap))[0]
        grid=np.linspace(lo,hi,17);scores=[objective(v) for v in grid]
        candidates=[(scores[0],lo),(scores[-1],hi)]
        for i in range(1,len(grid)-1):
            if scores[i]<=scores[i-1] and scores[i]<=scores[i+1]:
                f=optimize.minimize_scalar(objective,bounds=(grid[i-1],grid[i+1]),method='bounded',options={'xatol':1e-8})
                candidates.append((float(f.fun),float(f.x)))
        score,loggap=min(candidates)
        origin=minimum-span*math.exp(loggap)
        hit=bool(abs(loggap-lo)<1e-5 or abs(loggap-hi)<1e-5)
    else:
        origin=float(cfg['ORIGIN'])
        if not math.isfinite(origin) or origin>=minimum:
            raise ValueError('BOXCOX.ORIGIN must be finite and below all retained observations')
    score,lam=profile(origin)
    y=special.boxcox((x-origin)/span,lam)
    def inverse(v):return special.inv_boxcox(v,lam)*span+origin
    details=dict(transformation='shifted_Box_Cox_bounded_profile_MLE',lambda_value=lam,
                 origin=origin,normalization_scale=span,origin_at_boundary=hit,
                 lambda_at_boundary=bool(min(abs(lam-bounds))<1e-5),options=cfg)
    details['lambda']=lam
    return y,inverse,details


def gaussian_trim(values, method, z, boxcox=None):
    """One declared pass on the fitted scale; refit RI on the retained sample."""
    from .ri_ep28 import finite_sample, _transformation
    x=finite_sample(values,3)
    if not math.isfinite(z) or z<=0:raise ValueError('PARAMETRIC_TRIM_Z must be positive')
    if method not in {'PARAMETRIC','LOG_PARAMETRIC','BOXCOX_PARAMETRIC','BOXCOX_SHIFTED_PARAMETRIC'}:
        raise ValueError('Gaussian trimming requires a parametric primary method')
    y,_,details=_transformation(x,method,boxcox)
    sd=float(np.std(y,ddof=1));center=float(np.mean(y))
    mask=np.abs(y-center)>z*sd
    return mask,dict(z=z,location=center,scale=sd,transformation=details,passes=1,
                     criteria='strictly outside fitted mean +/- z*sample_SD; refit after removal')


def lave(values, names, *, targets, method='BOXCOX_PARAMETRIC', coverage=.95,
         quantile_type=6, max_abnormal=1, expansion=.05, iterations=6,
         min_n=40, missing_policy='ERROR', boxcox=None, normal_z=None):
    """Iterative cross-analyte exclusion with synchronous, auditable updates.

    Reconsider all original subjects at each update. Each reference test is fitted
    on people who pass the OTHER reference tests. Targets never screen themselves.
    Abnormal = strictly outside [LL-expansion*width, UL+expansion*width].
    Missing panels require explicit exclusion or an error; never count as normal.
    """
    from .ri_ep28 import estimate_limits
    x=np.asarray(values,dtype=float);names=list(names);targets=list(targets)
    if x.ndim!=2 or x.shape[1]!=len(names) or len(set(names))!=len(names) or len(names)<2:
        raise ValueError('LAVE requires at least two distinct reference tests')
    if not isinstance(max_abnormal,int) or isinstance(max_abnormal,bool) or not 0<=max_abnormal<len(names)-1:
        raise ValueError('LAVE MAX_ABNORMAL must leave at least one other reference test informative')
    if not math.isfinite(expansion) or expansion<0:raise ValueError('LAVE EXPANSION must be nonnegative')
    if not isinstance(iterations,int) or not 1<=iterations<=100:raise ValueError('LAVE ITERATIONS must be 1..100')
    if not isinstance(min_n,int) or min_n<3:raise ValueError('LAVE MIN_N must be >=3')
    if missing_policy not in {'ERROR','EXCLUDE'}:raise ValueError('LAVE MISSING_POLICY must be ERROR or EXCLUDE')
    complete=np.isfinite(x).all(axis=1)
    if not complete.all() and missing_policy=='ERROR':raise ValueError('LAVE reference panel contains missing values; declare MISSING_POLICY=EXCLUDE or resolve them')
    masks=np.repeat(complete[:,None],len(names),axis=1)
    history=[];last_flags=None;converged=False
    for iteration in range(iterations+1):
        bounds=[]
        for j,name in enumerate(names):
            sample=x[masks[:,j],j]
            if len(sample)<min_n:raise ValueError(f'LAVE {name}: n={len(sample)} below MIN_N={min_n}')
            est=estimate_limits(sample,method,coverage,quantile_type,boxcox=boxcox,normal_z=normal_z)
            if est.high<=est.low:raise ValueError(f'LAVE {name}: degenerate screening interval')
            width=est.high-est.low
            bounds.append([est.low-expansion*width,est.high+expansion*width])
            history.append(dict(iteration=iteration,reference_id=name,n=len(sample),ref_low=est.low,
                                ref_high=est.high,screen_low=bounds[-1][0],screen_high=bounds[-1][1],
                                details=est.details))
        bounds=np.asarray(bounds)
        flags=(x<bounds[:,0])|(x>bounds[:,1]);counts=flags.sum(axis=1)
        proposed=complete[:,None] & ((counts[:,None]-flags.astype(int))<=max_abnormal)
        last_flags=flags
        if np.array_equal(masks,proposed):converged=True;break
        if iteration==iterations:break
        masks=proposed
    selection={};counts_by_target={}
    for target in targets:
        count=last_flags.sum(axis=1)
        if target in names:count=count-last_flags[:,names.index(target)]
        selection[target]=complete & (count<=max_abnormal)
        counts_by_target[target]=count
    return dict(masks=selection,abnormal_counts=counts_by_target,complete=complete,
                history=history,converged=converged,updates=iteration,
                rule='cross-analyte; target excluded; synchronous updates; original cohort reconsidered')


def wilcoxon_partition(a,b):
    from .ri_ep28 import finite_sample
    a,b=finite_sample(a),finite_sample(b)
    r=stats.mannwhitneyu(a,b,alternative='two-sided',method='asymptotic',use_continuity=True)
    return dict(wilcoxon_u=float(r.statistic),wilcoxon_p=float(r.pvalue),
                wilcoxon_method='two-sided Mann-Whitney/Wilcoxon; asymptotic; tie and continuity corrections')


def variance_sdr(values, groups, nested=None):
    """ANOVA method-of-moments components, with unequal group sizes accounted for.

    Nested model: value = grand mean + outer + inner(outer) + residual.
    Negative variance estimates are truncated at zero and also reported raw.
    """
    from .ri_ep28 import finite_sample
    x=finite_sample(values,3);g=np.asarray(groups).astype(str)
    if g.shape!=x.shape:raise ValueError('SDR group labels must align with values')
    levels,inverse=np.unique(g,return_inverse=True)
    if len(levels)<2:raise ValueError('SDR requires at least two outer groups')
    outer=np.eye(len(levels))[inverse]
    if nested is None:inner=outer
    else:
        h=np.asarray(nested).astype(str)
        if h.shape!=x.shape:raise ValueError('Nested SDR labels must align with values')
        _,ix=np.unique(np.column_stack([g,h]),axis=0,return_inverse=True)
        inner=np.eye(int(ix.max())+1)[ix]
        if inner.shape[1]<=outer.shape[1]:raise ValueError('Nested SDR requires multiple inner groups within outer groups')
    n=len(x);a=outer.shape[1];b=inner.shape[1]
    if n<=b:raise ValueError('SDR requires residual degrees of freedom')
    u=outer/np.sqrt(outer.sum(axis=0));v=inner/np.sqrt(inner.sum(axis=0))
    total=float(x.sum()**2/n)
    ss_outer=float(np.dot(u.T@x,u.T@x)-total)
    ss_inner=float(np.dot(v.T@x,v.T@x)-np.dot(u.T@x,u.T@x))
    residual=x-inner@((inner.T@x)/inner.sum(axis=0))
    ms_e=float(residual@residual/(n-b))
    if ms_e<=0:raise ValueError('SDR residual variance must be positive')
    ms_outer=ss_outer/(a-1)
    a_outer=(np.square(u.T@outer).sum()-np.square(outer.sum(axis=0)).sum()/n)/(a-1)
    raw_inner=0.
    if nested is not None:
        a_inner=(np.square(v.T@inner).sum()-np.square(u.T@inner).sum())/(b-a)
        b_outer=(np.square(u.T@inner).sum()-np.square(inner.sum(axis=0)).sum()/n)/(a-1)
        raw_inner=(ss_inner/(b-a)-ms_e)/a_inner
        raw_outer=(ms_outer-ms_e-b_outer*raw_inner)/a_outer
    else:raw_outer=(ms_outer-ms_e)/a_outer
    result=dict(n=n,groups=a,residual_df=n-b,residual_variance=ms_e,
                between_variance_raw=float(raw_outer),between_variance=max(0.,float(raw_outer)),
                sdr=math.sqrt(max(0.,raw_outer)/ms_e),variance_method='ANOVA_expected_mean_squares_unbalanced',
                negative_component=bool(raw_outer<0 or raw_inner<0))
    if nested is not None:
        result.update(nested_groups=b,nested_variance_raw=float(raw_inner),nested_variance=max(0.,float(raw_inner)),
                      nested_sdr=math.sqrt(max(0.,raw_inner)/ms_e))
    return result


def bias_ratios(a_limits,b_limits,pooled_limits,reporting_unit=None):
    a,b,p=[np.asarray(v,dtype=float) for v in [a_limits,b_limits,pooled_limits]]
    if any(v.shape!=(2,) or not np.isfinite(v).all() or v[1]<=v[0] for v in [a,b,p]):
        raise ValueError('BR requires increasing finite intervals')
    sd=(p[1]-p[0])/3.92;delta=np.abs(a-b)
    result=dict(br_low=float(delta[0]/sd),br_high=float(delta[1]/sd),sd_ri=float(sd),
                delta_low=float(delta[0]),delta_high=float(delta[1]),br_threshold=.375)
    if reporting_unit is not None:
        ru=float(reporting_unit)
        if not math.isfinite(ru) or ru<=0:raise ValueError('REPORTING_UNIT must be positive')
        result.update(reporting_unit=ru,delta_low_ge_3ru=bool(delta[0]>=3*ru),delta_high_ge_3ru=bool(delta[1]>=3*ru))
    return result
