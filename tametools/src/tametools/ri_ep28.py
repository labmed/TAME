"""Numerical methods for explicit, auditable reference-interval studies.

Equations: Horn, Pesce & Copeland (1998), Harris & Boyd (1990), binomial
order-statistic intervals. Numerical cross-check: referenceIntervals 1.3.1.
This module implements statistical methods, not clinical adoption approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import warnings

import numpy as np
from scipy import special, stats


METHODS = {"NONPARAMETRIC", "PARAMETRIC", "LOG_PARAMETRIC", "BOXCOX_PARAMETRIC", "BOXCOX_SHIFTED_PARAMETRIC", "ROBUST", "LOG_ROBUST", "BOXCOX_ROBUST", "HARRELL_DAVIS"}
OUTLIERS = {"NONE", "TUKEY", "LOG_TUKEY", "HORN", "DIXON_Q", "REED", "COOK"}
CI_METHODS = {"AUTO", "RANK", "PARAMETRIC", "BOOTSTRAP", "NONE"}

# Upper-tail critical values at alpha/2, default n-dependent r10/r11/r21/r22.
# Numerical table cross-checked with outliers 0.15 qdixon; no R runtime needed.
# Dixon/Dean Q is distinct from the Reed one-third rule.
_DIXON = {
    .01: (.994,.926,.821,.740,.680,.725,.677,.639,.713,.675,.649,.674,.647,.624,
          .605,.589,.575,.562,.551,.541,.532,.524,.516,.508,.501,.495,.489,.483),
    .05: (.970,.829,.710,.625,.568,.615,.570,.534,.625,.592,.565,.590,.568,.548,
          .531,.516,.503,.491,.480,.470,.461,.452,.445,.438,.432,.426,.419,.414),
    .10: (.941,.765,.642,.560,.507,.554,.512,.477,.576,.546,.521,.546,.525,.507,
          .490,.475,.462,.450,.440,.430,.421,.413,.406,.399,.393,.387,.381,.376),
}


@dataclass
class Estimate:
    low: float
    high: float
    method: str
    details: dict = field(default_factory=dict)


@dataclass
class OutlierResult:
    mask: np.ndarray
    method: str
    details: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def finite_sample(values, minimum=2):
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or len(x) < minimum or not np.isfinite(x).all():
        raise ValueError(f"A one-dimensional sample of at least {minimum} finite values is required")
    return x


def probabilities(coverage):
    if not math.isfinite(coverage) or not 0 < coverage < 1:
        raise ValueError("COVERAGE must be strictly between 0 and 1")
    p = (1 - coverage) / 2
    return np.array([p, 1-p])


def _scale_biweight(x, center, bandwidth, multiplier):
    u = (x-center) / bandwidth
    u = u[np.abs(u) < 1]
    d = float(np.sum((1-u*u)*(1-5*u*u)))
    numerator = float(np.sum(u*u*(1-u*u)**4))
    denominator = d * max(1., d-1)
    if denominator <= 0 or numerator <= 0:
        raise ValueError("Robust scale is undefined; tied/degenerate data need review")
    return bandwidth * math.sqrt(multiplier*numerator/denominator)


def robust_estimate(values, coverage=.95, tolerance=1e-10, max_iterations=1000):
    """Horn/Pesce iterative biweight location and finite-sample prediction limits.

    Includes location uncertainty and Student t(n-1), rather than median +/- MAD.
    Scales use the original median; the final location scale uses the converged
    location. Fixed MAD normalization 0.6745 matches the published R comparator.
    """
    x = np.sort(finite_sample(values, 3))
    median = float(np.median(x))
    mad = float(np.median(np.abs(x-median))) / .6745
    if mad <= 0:
        raise ValueError("ROBUST requires positive MAD; no silent replacement method")
    center = median
    for iteration in range(1, max_iterations+1):
        u = (x-center)/(3.7*mad)
        w = np.square(np.maximum(0., 1-u*u))
        if w.sum() <= 0:
            raise ValueError("Robust location has zero total weight")
        updated = float(np.dot(x, w)/w.sum())
        if abs(updated-center) <= tolerance*mad:
            center = updated
            break
        center = updated
    else:
        raise ValueError("ROBUST did not converge within the iteration limit")
    broad = _scale_biweight(x, median, 205.6*mad, len(x))
    narrow = _scale_biweight(x, median, 3.7*mad, len(x))
    location_se = _scale_biweight(x, center, 3.7*narrow, 1)
    margin = float(stats.t.ppf(probabilities(coverage)[1], len(x)-1)) * math.hypot(broad, location_se)
    return Estimate(center-margin, center+margin, "ROBUST", dict(
        location=center, scale=broad, location_se=location_se, iterations=iteration,
        convergence_tolerance=tolerance, convergence_scale="initial_MAD_divided_by_0.6745",
        algorithm="Horn_Pesce_iterative_biweight"))


def _transformation(x, method, boxcox=None):
    if method == "BOXCOX_SHIFTED_PARAMETRIC":
        from .ri_study import shifted_boxcox
        return shifted_boxcox(x,boxcox)
    if method in {"LOG_PARAMETRIC","LOG_ROBUST"}:
        if np.any(x <= 0):
            raise ValueError("LOG_PARAMETRIC requires every included value to be positive")
        return np.log(x), np.exp, {"lambda": 0., "transformation": "log"}
    if method in {"BOXCOX_PARAMETRIC","BOXCOX_ROBUST"}:
        if np.any(x <= 0) or np.ptp(x) <= 0:
            raise ValueError("BOXCOX_PARAMETRIC requires positive, nonconstant values")
        lam = float(stats.boxcox_normmax(x, method="mle"))
        y = special.boxcox(x, lam)
        return y, lambda a: special.inv_boxcox(a, lam), {"lambda": lam, "transformation": "Box-Cox_MLE"}
    return x, lambda a: a, {"transformation": "identity"}


def estimate_limits(values, method="NONPARAMETRIC", coverage=.95, quantile_type=6, *, boxcox=None, normal_z=None):
    x = finite_sample(values)
    p = probabilities(coverage)
    if normal_z is not None and (not math.isfinite(normal_z) or normal_z<=0 or not math.isclose(coverage,.95)):
        raise ValueError("NORMAL_Z requires a positive finite value and COVERAGE=.95")
    if method not in METHODS:
        raise ValueError("Unknown reference-interval method: " + method)
    if quantile_type not in {6,7}:
        raise ValueError("QUANTILE_TYPE must be 6 (Weibull) or 7 (linear)")
    if method == "ROBUST":
        return robust_estimate(x, coverage)
    if method in {"LOG_ROBUST","BOXCOX_ROBUST"}:
        y,inverse,details=_transformation(x,method)
        e=robust_estimate(y,coverage)
        bounds=inverse(np.array([e.low,e.high]))
        details.update(e.details)
    elif method == "HARRELL_DAVIS":
        ordered=np.sort(x)
        edges=np.linspace(0,1,len(x)+1)
        bounds=[float(np.dot(ordered,np.diff(special.betainc((len(x)+1)*q,(len(x)+1)*(1-q),edges)))) for q in p]
        details={"algorithm":"Harrell_Davis_beta_weighted_order_statistics"}
    elif method == "NONPARAMETRIC":
        bounds = np.quantile(x, p, method="weibull" if quantile_type == 6 else "linear")
        details = {"quantile_type": quantile_type}
    else:
        transformed, inverse, details = _transformation(x, method, boxcox)
        mean, sd = float(np.mean(transformed)), float(np.std(transformed, ddof=1))
        z=stats.norm.ppf(p) if normal_z is None else np.array([-normal_z,normal_z])
        bounds = inverse(mean + z*sd)
        details.update(location=mean, scale=sd)
        if normal_z is not None:details["normal_z"]=normal_z
    if not np.isfinite(bounds).all() or bounds[0] > bounds[1]:
        raise ValueError("Reference limits are outside the transformation domain; review the distribution")
    return Estimate(float(bounds[0]), float(bounds[1]), method, details)


def rank_ci(values, coverage=.95, confidence=.90):
    x = np.sort(finite_sample(values))
    if not 0 < confidence < 1:
        raise ValueError("CI_LEVEL must be strictly between 0 and 1")
    tail = (1-confidence)/2
    intervals, ranks, attained = [], [], []
    for p in probabilities(coverage):
        lower = int(stats.binom.ppf(tail, len(x), p))
        upper = int(stats.binom.ppf(1-tail, len(x), p))+1
        intervals.append([float(x[lower-1]) if lower >= 1 else None,
                          float(x[upper-1]) if upper <= len(x) else None])
        ranks.append([lower, upper])
        attained.append(float(stats.binom.cdf(upper-1,len(x),p)-stats.binom.cdf(lower-1,len(x),p)))
    return {"bounds": intervals, "ranks": ranks, "attained_confidence": attained,
            "method": "binomial_order_statistics", "bootstrap_valid": 0,
            "warnings": (["Rank CI endpoint lies beyond observed sample; collect more reference individuals"]
                         if any(v is None for row in intervals for v in row) else [])}


def bootstrap_limits(values, method, coverage=.95, quantile_type=6, *, repetitions=2000, seed=20260911, indices=None, boxcox=None, normal_z=None):
    """Keep failed replicates explicit. Indices allow independent R validation."""
    x = finite_sample(values)
    if indices is not None:
        ix = np.asarray(indices)
        if ix.ndim != 2 or ix.shape[1] != len(x) or not np.issubdtype(ix.dtype, np.integer):
            raise ValueError("Bootstrap indices must be an integer matrix with n columns")
        if np.any(ix < 0) or np.any(ix >= len(x)):
            raise ValueError("Bootstrap index out of bounds")
        repetitions = len(ix)
    elif not isinstance(repetitions, int) or not 50 <= repetitions <= 20000:
        raise ValueError("BOOTSTRAP_N must be between 50 and 20000")
    rng = np.random.default_rng(seed)
    samples = np.full((repetitions,2), np.nan)
    # Bounded allocation; transformations are re-fitted within each resample.
    for i in range(repetitions):
        positions = ix[i] if indices is not None else rng.integers(0,len(x),size=len(x))
        try:
            e = estimate_limits(x[positions], method, coverage, quantile_type, boxcox=boxcox,normal_z=normal_z)
            samples[i] = [e.low,e.high]
        except (ValueError, FloatingPointError, OverflowError):
            pass
    return samples


def reference_ci(values, method, *, coverage=.95, confidence=.90, ci_method="AUTO",
                 quantile_type=6, repetitions=2000, seed=20260911, bootstrap_type="PERCENTILE", indices=None,
                 bootstrap_quantile="TYPE7", boxcox=None, normal_z=None):
    x = finite_sample(values)
    if not 0 < confidence < 1 or ci_method not in CI_METHODS:
        raise ValueError("Invalid CI_LEVEL or CI_METHOD")
    if ci_method == "AUTO":
        ci_method = "RANK" if method == "NONPARAMETRIC" and len(x)>=120 else (
            "PARAMETRIC" if method in {"PARAMETRIC","LOG_PARAMETRIC"} else "BOOTSTRAP")
    if ci_method == "NONE":
        return dict(bounds=[[None,None],[None,None]],method="not_requested",bootstrap_valid=0,warnings=["CI not requested"])
    if ci_method == "RANK":
        if method != "NONPARAMETRIC":
            raise ValueError("CI_METHOD=RANK requires METHOD=NONPARAMETRIC")
        return rank_ci(x,coverage,confidence)
    if ci_method == "PARAMETRIC":
        if method not in {"PARAMETRIC","LOG_PARAMETRIC"}:
            raise ValueError("Parametric CI is supported for normal and log-normal methods only")
        y, inverse, _ = _transformation(x,method)
        z = float(stats.norm.ppf(probabilities(coverage)[1])) if normal_z is None else float(normal_z)
        sd = float(np.std(y,ddof=1))
        # Asymptotic standard error, matching referenceIntervals::refLimit RI=p.
        se = sd*math.sqrt((1+z*z/2)/len(x))
        half = float(stats.norm.ppf((1+confidence)/2))*se
        centers = np.mean(y)+np.array([-z,z])*sd
        bounds = inverse(centers[:,None]+np.array([-half,half]))
        if not np.isfinite(bounds).all():
            raise ValueError("Nonfinite transformed confidence limit")
        return dict(bounds=bounds.tolist(),method="normal_limit_asymptotic",bootstrap_valid=0,
                    warnings=["Parametric CI uses a large-sample approximation on the fitted scale"])
    if bootstrap_type not in {"PERCENTILE","BASIC"}:
        raise ValueError("BOOTSTRAP_TYPE must be PERCENTILE or BASIC")
    samples=bootstrap_limits(x,method,coverage,quantile_type,repetitions=repetitions,seed=seed,indices=indices,boxcox=boxcox,normal_z=normal_z)
    valid=np.isfinite(samples).all(axis=1)
    if int(valid.sum()) < max(50,math.ceil(.95*len(samples))):
        raise ValueError(f"Too many invalid bootstrap replicates: {valid.sum()}/{len(samples)}")
    if bootstrap_quantile not in {"TYPE7","R_BOOT"}:raise ValueError("BOOTSTRAP_QUANTILE must be TYPE7 or R_BOOT")
    probs=[(1-confidence)/2,(1+confidence)/2]
    if bootstrap_quantile=="TYPE7":q=np.quantile(samples[valid],probs,axis=0,method="linear").T
    else:q=np.array([r_boot_quantile(samples[valid,j],probs) for j in range(2)])
    if bootstrap_type == "BASIC":
        e=estimate_limits(x,method,coverage,quantile_type,boxcox=boxcox,normal_z=normal_z)
        q=2*np.array([e.low,e.high])[:,None]-q[:,::-1]
    return dict(bounds=q.tolist(),method="bootstrap_"+bootstrap_type.lower()+("_quantile_type7" if bootstrap_quantile=="TYPE7" else "_R_boot_normal_interpolation"),
                bootstrap_mean=samples[valid].mean(axis=0).tolist(),bootstrap_quantile=bootstrap_quantile,
                bootstrap_valid=int(valid.sum()),bootstrap_failed=int((~valid).sum()),
                warnings=([f"{int((~valid).sum())} invalid bootstrap replicates omitted"] if not valid.all() else []))


def detect_outliers(values, method="NONE", *, tukey_k=1.5, alpha=.05, reed_iterations=1, reed_block_max=1):
    x=finite_sample(values)
    if method not in OUTLIERS:
        raise ValueError("Unknown OUTLIER_METHOD: "+method)
    result=OutlierResult(np.zeros(len(x),dtype=bool),method)
    if method == "NONE":
        return result
    if method == "DIXON_Q" and not 3 <= len(x) <= 30:
        raise ValueError("DIXON_Q requires 3 <= n <= 30; REED is a separate, explicitly named rule")
    if not math.isfinite(tukey_k) or tukey_k<=0:
        raise ValueError("TUKEY_K must be positive and finite")
    if np.ptp(x)==0:
        result.warnings.append("Constant sample: outlier detection is uninformative")
        return result
    if method in {"TUKEY","LOG_TUKEY","HORN"}:
        if len(x)<4:
            raise ValueError("Tukey/Horn outlier detection requires at least four values")
        y=x
        if method in {"LOG_TUKEY","HORN"} and np.any(x<=0):
            raise ValueError(method+" requires all values to be positive; no automatic shift")
        if method == "LOG_TUKEY":
            y=np.log(x)
        if method == "HORN":
            grid=np.linspace(-2.,2.,41)
            lam=float(grid[int(np.argmax([stats.boxcox_llf(v,x) for v in grid]))])
            y=special.boxcox(x,lam)
            result.details.update(lambda_grid=lam,lambda_boundary=bool(abs(lam)==2.))
            if abs(lam)==2.:
                result.warnings.append("Horn Box-Cox optimum is at the search-grid boundary")
        q1,q3=np.quantile(y,[.25,.75],method="linear")
        lo,hi=float(q1-tukey_k*(q3-q1)),float(q3+tukey_k*(q3-q1))
        result.details.update(lower_fence=lo,upper_fence=hi,fence_scale=method,tukey_k=tukey_k,
                              quantile_type=7,boundary="inclusive" if method=="HORN" else "strict")
        if q3==q1:
            result.warnings.append("IQR is zero; automatic fence rejection suppressed")
            return result
        # Horn's R implementation includes the fence itself in the flagged set.
        result.mask=(y<=lo)|(y>=hi) if method=="HORN" else (y<lo)|(y>hi)
    elif method == "COOK":
        n=len(x)
        mse=float(np.sum((x-x.mean())**2)/(n-1))
        distance=(x-x.mean())**2/mse*(1/n)/(1-1/n)**2
        threshold=min(4/n,1.)
        result.mask=distance>threshold
        result.details.update(threshold=threshold,max_distance=float(distance.max()),model="intercept_only")
    elif method == "REED":
        if isinstance(reed_iterations,bool) or not isinstance(reed_iterations,int) or not 1<=reed_iterations<=10:
            raise ValueError("REED_ITERATIONS must be an integer from 1 to 10")
        if type(reed_block_max) is not int or not 1<=reed_block_max<=3:
            raise ValueError("REED_BLOCK_MAX must be 1, 2, or 3")
        passes=[]
        for iteration in range(reed_iterations):
            order=np.flatnonzero(~result.mask)
            order=order[np.argsort(x[order],kind="stable")]
            if len(order)<3 or x[order[-1]]==x[order[0]]:
                break
            flagged=[]; trials=[]
            for k in range(1,min(reed_block_max,len(order)-2)+1):
                low_span=x[order[-1]]-x[order[k-1]]
                high_span=x[order[-k]]-x[order[0]]
                low=float((x[order[k]]-x[order[k-1]])/low_span) if low_span>0 else 0.
                high=float((x[order[-k]]-x[order[-k-1]])/high_span) if high_span>0 else 0.
                trials.append(dict(block_size=k,low_ratio=low,high_ratio=high))
                if low>=1/3:flagged.extend(int(v) for v in order[:k])
                if high>=1/3:flagged.extend(int(v) for v in order[-k:])
            flagged=sorted(set(flagged))
            passes.append(dict(iteration=iteration+1,trials=trials,positions=flagged))
            result.mask[flagged]=True
            if not flagged:break
        result.details.update(threshold=1/3,boundary="inclusive",block_max=reed_block_max,passes=passes)
        if len(passes)>1:
            result.warnings.append("Repeated Reed screening can over-trim tails; all passes are audited")
        if passes and passes[-1]["positions"] and len(passes)==reed_iterations:
            result.warnings.append("Reed pass limit reached after flags; review and re-screen the remaining observations before accepting exclusions")
    else:
        if alpha not in _DIXON:
            raise ValueError("DIXON_ALPHA must be .01, .05, or .10")
        order=np.argsort(x,kind="stable")
        y=x[order]
        n=len(x)
        low_side=bool(y[-1]-np.mean(y)<np.mean(y)-y[0])
        if not low_side:y=-y[::-1]
        gap=1 if n<=10 else 2
        trim=0 if n<=7 else (1 if n<=13 else 2)
        denominator=y[n-1-trim]-y[0]
        q=float((y[gap]-y[0])/denominator) if denominator>0 else math.nan
        critical=_DIXON[alpha][n-3]
        if not math.isfinite(q):
            result.warnings.append("Dixon denominator is zero; no automatic exclusion")
        elif q>=critical:
            extreme=x[order[0] if low_side else order[-1]]
            result.mask=x==extreme
        result.details.update(statistic=q if math.isfinite(q) else None,critical=critical,
                              alpha=alpha,two_sided=True,type=gap*10+trim,
                              side="low" if low_side else "high")
    return result


def normality(values):
    x=finite_sample(values)
    if np.ptp(x)==0:
        return dict(n=len(x),shapiro_w=None,shapiro_p=None,skewness=None,status="constant")
    if 3<=len(x)<=5000:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            w,p=stats.shapiro(x)
        status="review_non_normal" if p<.05 else "not_rejected_not_proof"
    else:
        w,p=None,None
        status="shapiro_not_evaluated_n_outside_3_5000"
    return dict(n=len(x),shapiro_w=None if w is None else float(w),
                shapiro_p=None if p is None else float(p),
                skewness=float(stats.skew(x,bias=False)) if len(x)>2 else None,status=status)


def harris_boyd(a,b,*,scale="RAW"):
    a,b=finite_sample(a,3),finite_sample(b,3)
    if scale not in {"RAW","LOG"}:
        raise ValueError("PARTITION_SCALE must be RAW or LOG")
    if scale=="LOG":
        if np.any(a<=0) or np.any(b<=0):
            raise ValueError("Log partition assessment requires positive observations")
        a,b=np.log(a),np.log(b)
    sa,sb=float(a.std(ddof=1)),float(b.std(ddof=1))
    critical=3*math.sqrt((len(a)+len(b))/240)
    if min(sa,sb)<=0:
        return dict(z=None,z_critical=critical,sd_ratio=None,decision="not_evaluable_zero_variance",scale=scale)
    z=float(abs(a.mean()-b.mean())/math.sqrt(sa*sa/len(a)+sb*sb/len(b)))
    ratio=max(sa,sb)/min(sa,sb)
    small=min(len(a),len(b))<120
    rejected=any(normality(v)["status"]=="review_non_normal" for v in [a,b])
    return dict(z=z,z_critical=critical,sd_ratio=ratio,scale=scale,
                statistical_signal=bool(z>critical or ratio>1.5),
                decision="review_partition" if z>critical or ratio>1.5 else "no_statistical_signal",
                small_subgroup=small,normality_rejected=rejected,
                unbalanced_ratio=max(len(a),len(b))/min(len(a),len(b)))


def tail_proportions(values, low, high, confidence=.90):
    x=finite_sample(values)
    if not math.isfinite(low) or not math.isfinite(high) or low>=high:
        raise ValueError("Pooled reference limits must be finite and increasing")
    rows=[]
    for side,k in [("lower",int((x<low).sum())),("upper",int((x>high).sum()))]:
        interval=stats.binomtest(k,len(x)).proportion_ci(confidence_level=confidence,method="exact")
        proportion=k/len(x)
        # Observed tails are descriptive; 4.1/3.2% are supplementary Lahti criteria.
        signal="review_partition" if proportion> .041 else ("marginal" if proportion> .032 else "no_excess_tail_signal")
        rows.append(dict(tail=side,outside_n=k,n=len(x),proportion=proportion,
                         ci_low=float(interval.low),ci_high=float(interval.high),signal=signal))
    return rows


def verify_twenty(values,low,high,*,batch=1,first_batch_outside=None):
    x=finite_sample(values)
    if len(x)!=20:
        raise ValueError("EP28 verification requires exactly 20 eligible independent individuals per batch")
    if not math.isfinite(low) or not math.isfinite(high) or low>=high:
        raise ValueError("Verification needs finite, increasing predefined reference limits")
    if batch not in {1,2}:
        raise ValueError("VERIFY_BATCH must be 1 or 2")
    if batch==2 and first_batch_outside not in {3,4}:
        raise ValueError("Second batch is permitted only after 3 or 4 outside results in the first 20")
    if batch==1 and first_batch_outside is not None:
        raise ValueError("FIRST_BATCH_OUTSIDE is only valid for batch 2")
    lower,upper=int((x<low).sum()),int((x>high).sum())
    outside=lower+upper
    decision=("verification_criterion_met" if outside<=2 else
              "collect_second_batch_of_20" if batch==1 and outside<=4 else "verification_criterion_not_met")
    return dict(n=20,batch=batch,lower_outside=lower,upper_outside=upper,outside_n=outside,
                ref_low=low,ref_high=high,decision=decision,first_batch_outside=first_batch_outside)


def r_boot_quantile(values,probs):
    """Normal-quantile interpolation used by boot::boot.ci (percentile/basic)."""
    x=np.sort(finite_sample(values));result=[];n=len(x)
    for p in probs:
        if not 0<p<1:raise ValueError('Bootstrap tail probability must be between 0 and 1')
        rank=(n+1)*p;k=int(rank)
        if k<1:value=x[0]
        elif k>=n:value=x[-1]
        elif math.isclose(rank,k,abs_tol=1e-12):value=x[k-1]
        else:
            a,b=stats.norm.ppf([k/(n+1),(k+1)/(n+1)])
            value=x[k-1]+(stats.norm.ppf(p)-a)/(b-a)*(x[k]-x[k-1])
        result.append(float(value))
    return result
