# TAME Analysis Contract 1

2026-09-11. Implemented optional extension to the existing TSV/TOML container,
not a claim that all historical V2 proposals have been implemented.

## Scope

The container grammar and cell-state tokens are unchanged. Existing files without
the new declarations retain their numeric-analysis behavior. New consumers must
check `ANALYSIS_CONTRACT.VERSION = 1` before executing the bindings. Older readers
may preserve unknown metadata, but cannot be assumed to execute these operations.
This contract covers numeric source codes for left-censoring and one-stage
stratified cluster, with-replacement Taylor inference. It is not a generic survey,
unit ontology, reference-interval, or censored-data estimation specification.

## Stable Column Identity

`COLUMN.<display name>.ID` is an optional, nonempty, case-sensitive string, unique
within a dataset. It identifies a variable, not a participant. References ending
in `_ID` below resolve this field, never the displayed header or the first matching
tag. Renaming a header requires renaming its `COLUMN` table key, but not IDs or
references. Unknown and duplicate IDs fail validation or execution.

An ID is author-declared identity, not evidence of equivalent analytes, specimens,
methods or populations. `merge_datasets` prioritizes explicit IDs; without IDs,
legacy unambiguous-tag/name matching remains available. Matched columns with
different declared `UNIT`, an unknown unit on only one side, or conflicting
column metadata fail rather than silently taking the first file's metadata.
Metadata follows the selected output name. Automatic concatenation of multiple
declared survey designs is rejected: weights and reused strata/PSUs require an
explicit analytic decision. These deliberate fail-fast changes also apply to
legacy files that declare conflicting column metadata.

## Released Values And Censoring

Example metadata for the observed NHANES hsCRP variables:

```toml
[ANALYSIS_CONTRACT]
VERSION = 1

[COLUMN.released]
ID = "hscrp_released"
UNIT = "mg/L"
SOURCE_VARIABLE = "LBXHSCRP"

[COLUMN.flag]
ID = "hscrp_flag"
SOURCE_VARIABLE = "LBDHRPLC"

[COLUMN.result]
ID = "hscrp_analysis"
UNIT = "mg/L"
ELIGIBLE_ID = "eligible"

[COLUMN.result.CENSORING]
SOURCE_ID = "hscrp_released"
FLAG_ID = "hscrp_flag"
LIMIT = 0.15
BELOW_CODE = 1
OBSERVED_CODE = 0
```

The source has `NUM` and the derived result has `RESULT::<NUM>` tags. Source,
flag and result are distinct columns. All five CENSORING keys are required;
unknown keys fail. The limit is positive and finite, codes finite and distinct.
Source and result must declare identical units, interpreted as literal
case-sensitive strings. There is no inferred unit conversion or UCUM validation.

`CENSOR` copies observed source values and states to the existing derived column,
replacing only below-limit rows by `<LIMIT`. It never overwrites the source.
For hsCRP the released 0.11 remains available; the derived value becomes `<0.15`.
The released number and flag must have matching missingness; unknown codes,
nonfinite/nonnumeric source values and an observed-code value below the limit fail.
`VALIDATE` and survey execution also reject a stale/tampered derived value.
Global binding issues use row number 0 and are not repaired by row deletion.

Survey policies are explicit and not interchangeable:

| Policy | Below-limit contribution | Meaning |
| --- | --- | --- |
| RELEASED | Unchanged source-supplied number | Reproduce released fill-value analysis |
| VALUE | Declared detection limit | Threshold-substitution sensitivity analysis |
| DELETE | No contribution to numerator or denominator | Observed, noncensored response domain |

None recovers the unknown concentration. The absence of a released flag does not
establish that an assay has no censoring. With no CENSORING binding, survey inputs
must be ordinary finite numeric results (apart from declared non-value states).
Implicit comparator parsing under RELEASED is prohibited. Existing `describe`
still analyzes the stored derived `<NUM>` representation with its existing CRR
rules; the new three-policy comparison belongs to `SURVEY_DESCRIBE`.

## Survey Design And Domains

```toml
[SURVEY]
DESIGN = "STRATIFIED_CLUSTER_WR"
WEIGHT_ID = "weight"
STRATUM_ID = "stratum"
PSU_ID = "psu"
EXPECTED_ROWS = 9254

[SURVEY_DESCRIBE]
RESULT_IDS = ["hscrp_analysis"]
DOMAIN_IDS = ["adults_20_plus", "male_20_plus", "female_20_plus", "age_80_plus"]
POLICIES = ["RELEASED", "VALUE", "DELETE"]

[WORKS]
DEFAULT = ["CENSOR", "VALIDATE", "SURVEY_DESCRIBE"]
```

All referenced variables require corresponding unique COLUMN IDs. Design
variables are distinct. `EXPECTED_ROWS` is optional, but when supplied must equal
the input row count. It guards against inadvertent prefiltering; matching counts
alone cannot prove that the full correct design was supplied.

Weights must be finite, nonnegative and nonmissing. Zero weights are explicitly
counted and excluded from the design. Positive-weight records require nonmissing
strata and PSU identifiers. PSU identity is the `(stratum, PSU)` pair. A full-design
stratum with fewer than two PSUs raises an error; no silent lonely-PSU adjustment
is made. FPC, replicate weights, additional sampling stages and unrecognized
design options are rejected, not ignored.

Each `DOMAIN_ID`, and optional per-result `ELIGIBLE_ID`, identifies an explicit
0/1 column without missing values. A result's eligibility is intersected with
each requested domain. `ALL` means the eligible population, not every age or
every row in a joined file. For the example, eligibility is age >=12 for BIOPRO_J
and age >=1 for HSCRP_J. Outcome missingness and policy exclusions are applied
as response-domain indicators after retaining the positive-weight design.
The 80+ domain uses the released top code as a group, never as exact age 80.

## Estimation And Output

The optional dependency `tametools[survey]` pins `samplics==0.4.55`. The implementation
uses its `TaylorEstimator(PopParam.ratio)`: the numerator is `I*y`, the denominator
is `I`, and out-of-domain or missing observations contribute zero to both while
remaining in the design. This is the ratio form of the weighted domain mean.
Taylor SE uses the full set of positive-weight strata/PSUs. The 95% interval uses
SciPy's Student t quantile, with degrees of freedom equal to represented PSUs minus
represented strata in the response domain, not a normal approximation or
frequency-weight standard error. Empty domains have no mean or CI; domains with
no positive degrees of freedom have no CI and a diagnostic status.

The returned OperationOutput table includes result ID and current header, unit,
policy, domain, eligibility ID, full design and zero-weight counts, domain count,
analyzed count, missing and below-limit counts, analyzed weight sum, mean, SE,
CI, degrees of freedom, status, engine version and variance method. Without a
released flag, `below_llod_n = 0` is a count of identified flags, not proof of no
censoring; the case-study export additionally records flag availability.
The standard workflow log records explicit options and warnings. Hashes of input
files and implementation snapshots are supplied separately; the older integrity
stamp does not authenticate this metadata.

API: `tametools.survey.survey_describe(dataset)`.
CLI: `tametools run example.tame DEFAULT --output processed.tame`.
Survey analysis is opt-in; `describe` does not silently become population-weighted.

## Limits And References

The implementation does not assess whether the supplied weight is scientifically
appropriate, automatically pool NHANES cycles, adjust laboratory item nonresponse,
infer selection mechanisms, age-standardize estimates, diagnose health status, or
estimate clinical reference intervals. Using a recognized engine does not remove
these study-design responsibilities. The numerical oracle shares only SciPy's
t quantile, not the binding/parser or samplics point/variance computations.

- CDC/NCHS. [NHANES variance estimation tutorial](https://wwwn.cdc.gov/nchs/nhanes/tutorials/varianceestimation.aspx).
- CDC/NCHS. [2017-2018 biochemistry documentation](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/BIOPRO_J.htm).
- CDC/NCHS. [2017-2018 hsCRP documentation](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/HSCRP_J.htm).
- Diallo MS. [samplics](https://doi.org/10.21105/joss.03376). J Open Source Softw. 2021;6(68):3376.
