# TAME Analysis Plan 1

**tametools 0.3.0 extension:** Optional MODELS and MISSINGNESS tables, observation and
result-status contracts, per-row interval censoring, and effective-input audit
snapshots are specified in [TAME analysis safety](TAME_ANALYSIS_SAFETY_V1.md).
The base examples below remain valid. Their sample correlations and percentiles
remain descriptive; they are not substituted for the new explicit model outputs.

Optional local extension, 2026-09-11. The TSV/TOML container and existing analysis,
survey and integration contracts remain unchanged. This contract declares routine
chemistry exploration, not a new statistical estimator or clinical decision rule.

The public command is `analyze FILE [PLUGIN]`. Without PLUGIN it executes
ANALYSIS_PLAN; with PLUGIN it preserves the existing named-plugin behavior.
Named-plugin options and plan/report options cannot be mixed. The old `clinical`
CLI command is removed. Legacy CLINICAL_ANALYSIS metadata/workflow steps and
Python imports remain readable for earlier artifacts; new outputs use
ANALYSIS_PLAN and the ANALYZE operation. Declaring both old and new plan tables,
including case-variant duplicates, is an error. WORKS uses `ANALYZE`, with its
configuration exclusively in ANALYSIS_PLAN; the full executed plan is logged.

## Executable Plan

Declare `ANALYSIS_CONTRACT.VERSION=1`, unique `COLUMN.ID` values and an explicit
`ANALYSIS_PLAN` table. Analysis structural keys are uppercase; unknown keys are
errors. An optional standalone TOML plan can be supplied with `analyze --plan`.
The actual resolved plan is retained in the report manifest, including overrides.

```toml
[ANALYSIS_PLAN]
VERSION = 1
MODE = "SURVEY"
RESULT_IDS = ["alt", "creatinine", "hscrp"]
POLICIES = ["RELEASED", "VALUE", "DELETE"]
PRIMARY_POLICY = "RELEASED"
PLOT_GROUP = "adults"
PLOT_RESULT_IDS = ["alt", "hscrp"]
HISTOGRAM_BINS = 20
PAIRS = [{ ID="alt_hscrp", X_ID="alt", Y_ID="hscrp" }]
CONTRASTS = [{ ID="men_minus_women", GROUP_A="men_adults", GROUP_B="women_adults" }]

[ANALYSIS_PLAN.HISTOGRAM_SCALES]
alt = "log10"
hscrp = "log10"

[ANALYSIS_PLAN.GROUPS.adults]
ALL_OF = [{ COLUMN_ID="age", MIN=20 }]
[ANALYSIS_PLAN.GROUPS.men_adults]
ALL_OF = [{ COLUMN_ID="age", MIN=20 }, { COLUMN_ID="sex_code", IN_NUMBERS=[1] }]
[ANALYSIS_PLAN.GROUPS.women_adults]
ALL_OF = [{ COLUMN_ID="age", MIN=20 }, { COLUMN_ID="sex_code", IN_NUMBERS=[2] }]

[WORKS]
DEFAULT = ["CENSOR", "VALIDATE", "ANALYZE"]
```

The example assumes confirmed numeric age in years and the stated source sex
codes. For other sources, review units and categorical codes rather than copying
these meanings. In a standalone plan omit the `ANALYSIS_PLAN` prefix and the
WORKS table; the supplied NHANES `analysis_plan.toml` is a complete example.

Required plan fields are VERSION, MODE, POLICIES and PRIMARY_POLICY, plus exactly
one of RESULT_IDS or RESULT_TAGS. RESULT_TAGS is a nonempty conjunction of tag
tokens (including inheritance), for example `["RESULT", "PANEL(chemistry)"]`.
Every match must have a stable COLUMN.ID and satisfy the quantitative-result
contract below. Matches are ordered by stable ID, not display name or position.
Empty matches and normalized-duplicate/malformed tags fail. The manifest retains
the tag plan and separately records `resolved_result_ids`; IDs are not silently
inserted into or substituted for the user's plan. Existing RESULT_IDS behavior is
unchanged. See [measurement tags](TAME_MEASUREMENT_TAGS.md).
Optional fields are GROUPS, PAIRS, CONTRASTS, MODELS, MISSINGNESS, PLOT_GROUP, PLOT_RESULT_IDS,
HISTOGRAM_BINS and HISTOGRAM_SCALES. Lists of results/policies must be nonempty
and distinct. Pair and contrast arrays may be empty. Group/pair/contrast IDs use
`[A-Za-z][A-Za-z0-9_]{0,63}`. Internal `__analysis_plan_` column names/IDs are reserved.

MODE is explicitly SURVEY or SAMPLE. SURVEY requires a SURVEY declaration;
SAMPLE refuses an input that still declares SURVEY. This prevents silent fallback
to ordinary independent-observation inference. The survey adapter retains the
existing STRATIFIED_CLUSTER_WR design checks, positive weights, full-row count,
strata and nested PSU identity, singleton-stratum rejection and pinned samplics
0.4.55 engine. There is no automatic selection of a weight variable or survey-cycle
combination. See the [NHANES weighting guidance](https://wwwn.cdc.gov/nchs/nhanes/tutorials/weighting.aspx).

## Results and Eligibility

Each result ID must identify a RESULT column with NUM or <NUM> and a nonempty UNIT.
NOMINAL and ORDINAL results are not quantitative selections, even with a NUM tag.
AGE is not a chemistry result. Raw values are not selected merely because they are
numeric. No conversion or pooling of different units is performed by this analysis
step; use the separately reviewed integration contract when necessary.

```toml
[COLUMN.result_alt]
ID = "alt"
UNIT = "U/L"
[COLUMN.result_alt.ELIGIBILITY]
ALL_OF = [{ COLUMN_ID="age", MIN=12 }]

[COLUMN.age]
ID = "age"
UNIT = "years"
TOP_CODE_VALUE = 80
```

ELIGIBILITY is optional and contains exactly ALL_OF. The existing binary
ELIGIBLE_ID binding is also supported, but not simultaneously with ELIGIBILITY.
Without either, all rows are eligible. Missing eligibility conditions are excluded
and reported, not silently treated as known ineligibility.

ALL_OF is a nonempty list of conjunctions. A condition uses a COLUMN_ID plus either
MIN and/or MAX_EXCLUSIVE, IN_NUMBERS, or IN_TEXT. Numeric cut points/codes must be
finite TOML numbers, not booleans. Numeric predicates reject nonnumeric present
cells. MIN is inclusive and MAX_EXCLUSIVE is exclusive, in the column's declared
storage units. IN_NUMBERS explicitly matches numeric codes, including equivalent
stored numeric spellings. IN_TEXT uses exact strings, without synonym, whitespace
or case normalization. Non-VALUE states never satisfy a condition. Different
groups may overlap; they are not required to partition the sample. ALL is a
reserved built-in group and cannot be redefined. No arbitrary expressions or code
are evaluated.

TOP_CODE_VALUE can declare a numeric released top code. Bounds above that code
are rejected because the stored value cannot resolve a narrower upper age range.
For NHANES age 80, `[80, infinity)` is supported; `[80,90)` and `[85,infinity)` are
not. Membership in the released code remains a code-based selection, not an exact
age. The engine does not compute age means or infer unreported ages.

## Censoring and Value States

Existing CENSORING source/flag bindings are verified. RELEASED uses the released
values, VALUE substitutes the declared boundary, and DELETE excludes censored
observations. For an explicit comparator without a released-value binding,
RELEASED fails; VALUE and DELETE are available only with an explicit <NUM> tag.
Left and right comparator thresholds retain their meaning in the source dataset;
they are not treated as recovered concentrations. No censored regression or
distribution model is fitted. Numeric chemistry parsing follows the existing
ordinary-decimal grammar; unsupported text is an error, not silent missingness.

ABSENT, NULL, EMPTY and WS are retained in the input. The summary distinguishes
missing result values, known censored observations, and policy-driven exclusions.
Censoring status is source_flag, explicit_comparator or not_reported. Absence of
a released flag is not evidence that there are no censored measurements.

`analyze` verifies rather than repairs stale derived values. Use the explicit
CENSOR workflow step when deriving from released values/flags. Input observations
are never changed by ANALYZE itself. The returned dataset records the
resolved plan without overwriting the original object's metadata.

## Output Tables

| Table | Meaning |
|---|---|
| groups | Raw group membership, positive-weight membership, any missing predicate and the explicit definition |
| sample_summary | Per result/group/policy counts, mean, sample SD, median, Q1/Q3, 2.5/97.5 percentiles, min/max, CV, geometric mean/SD |
| survey_means | Separately labelled weighted means, Taylor SE, 95% t CI, weights, design/domain sizes, degrees of freedom and status |
| survey_contrasts | Primary-policy group A minus group B means with shared-PSU covariance, Taylor SE and unadjusted 95% CI |
| correlations | Pairwise-complete sample Pearson r and Spearman rho, both units, eligibility and excluded-pair counts; no population p-values |
| histograms | Unweighted primary-policy bin counts, original-unit edges, linear/log10 scale and final-bin closure |

In SURVEY mode the sample tables describe positive-weight records in each eligible
domain but remain **unweighted sample statistics**. They are not weighted population
quantiles/correlations. In SAMPLE mode all input records contribute according to
eligibility and policy. The count identity is
`domain_n = analysis_n + missing_n + policy_excluded_n`;
censored_n overlaps analysis_n under RELEASED/VALUE. eligible_n includes zero-weight
rows while domain_n excludes them. Pairwise analysis uses joint eligibility and
complete observations of both results, not one global complete-case filter.

Sample SD uses n-1. Quantiles use linear interpolation (NumPy's linear method).
CV is emitted only for a positive mean and is **between-observation sample
dispersion**, not analytical QC imprecision. Geometric summaries require all usable
values to be positive; otherwise they remain undefined with a status, without
dropping zeros or negative observations. Empty/singleton and constant-variable
cases are explicit. SciPy supplies the
[Pearson](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.pearsonr.html)
and [Spearman](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.spearmanr.html)
coefficients. Ordinary population p-values are deliberately not reported for
the unweighted correlations.

Survey means reuse the existing full-design ratio estimator. Internal numeric
analysis columns encode the selected policy and eligibility; these temporary
columns never enter the returned data. Out-of-domain records remain in the design,
consistent with [NHANES domain analysis guidance](https://wwwn.cdc.gov/nchs/nhanes/tutorials/varianceestimation.aspx).

For a contrast, let Dg = sum(w I_g) and mug = sum(w I_g y)/Dg for each complete-case
group. The linearized unweighted input to the Taylor total estimator is
`I_A*(y-muA)/D_A - I_B*(y-muB)/D_B`. Samplics estimates its weighted total variance
using all positive-weight strata/PSUs. Joint linearization retains covariance,
including overlapping groups. The point estimate is muA-muB. The adapter uses the
smaller of the two represented-domain degrees of freedom for the t interval, an
explicit conservative convention rather than a claimed universal contrast rule.
Empty groups or nonpositive degrees of freedom do not produce confidence limits.
Intervals are componentwise, unadjusted and primary-policy only. No multiplicity
adjustment, age-adjusted regression or causal effect is implied.

## Graphs and Reports

PRIMARY_POLICY governs contrasts and graphs, not the availability of sensitivity
tables. PLOT_GROUP defaults to ALL; PLOT_RESULT_IDS defaults to every result.
An empty plotting-result list suppresses marginal histograms and mean charts, not
explicitly requested pair scatterplots. HISTOGRAM_BINS defaults to 20 (allowed
5-100). HISTOGRAM_SCALES maps selected result IDs to linear or log10, defaulting to
linear. A log histogram fails on nonpositive usable observations instead of
silently deleting them. Binning occurs in log10 space; reported edges are
back-transformed and bar heights are counts per bin, not densities.

All usable observations enter histograms/scatterplots. There is no automatic
outlier removal, winsorization, first-80-row plot truncation or log transformation
of the reported statistics. Numeric scatter x coordinates remain numeric.
Survey mean charts display declared CI bounds. X_LABEL, Y_LABEL, Y_LOW and Y_HIGH
are preserved in visualization metadata. Plot labels include units and scope.

```bash
tametools analyze nhanes.tame --output-dir new-report --docx
tametools analyze nhanes.xlsx --plan reviewed_plan.toml --output-dir another-report
tametools run nhanes.tame --output completed_workflow.tame
```

The analyze command uses a new output directory and refuses overwrites. It saves
full-precision CSVs, PNGs and a manifest with the executed plan, input file/cell
hashes, implementation-module hashes, package versions and output hashes. Word
reports are optional and present compact, primary-policy tables; all policies
remain in the CSVs. The report does not automatically print the first rows of an
identified input dataset. Inputs are not anonymized; scatterplots still represent
individual observations. Protect real clinical data through separate governance.

Install the local extra `tametools[analysis]`. Integration input validation may also
require `tametools[integration]`. Reproducible example pins are supplied separately.
This is a local source extension, not a claim that the old public MSI contains it.

## NHANES Scope

The supplied example links DEMO_J, BIOPRO_J and HSCRP_J by unique SEQN with the full
DEMO_J sample retained. Eligibility follows source age coverage; native source
values and flags remain alongside derived results. The 80+ age code is preserved.
The [BIOPRO_J](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/BIOPRO_J.htm)
glucose variable is not the separate fasting-glucose subsample. The
[HSCRP_J](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/HSCRP_J.htm)
released fill value is not an observed concentration below the detection limit.

These are complete-case descriptive examples without additional analyte-specific
nonresponse weighting. Percentiles are not clinical reference intervals, and
associations are not diagnoses. The data do not establish analytical QC precision,
delta checks, longitudinal change, independent method agreement, causal effects
or real multicenter assay equivalence. Those tasks need appropriate additional
data and independently reviewed methods.
