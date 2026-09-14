# TAME Integration Contract 1

**Version note (tametools 0.3.0):** This page documents the original version-1 output.
Existing version-1 files remain readable and verifiable. New output uses
INTEGRATION_RESULT.VERSION=2 with reviewed target tags, result status and interval
bindings. Eq/L, mEq/L, %, mmol/mol, reviewed aliases and directed affine bridges
are now supported within the stricter scope in
[the current analysis safety contract, section 8](TAME_ANALYSIS_SAFETY_V1.md).
The older limitations below describe version 1, not the full 0.3.0 feature set.

Status: optional local reference-implementation extension, 2026-09-11.
This does not change the TSV/TOML container grammar or imply a public MSI release.

## Purpose and Boundary

Integrate explicitly mapped, declared-compatible quantitative measurements from
different exports while retaining their original cell text, states and controls.
Equal units, column names, general RESULT tags or shared terminology codes alone
do not establish analytical equivalence. No patient linkage, deduplication,
assay calibration correction, pooled reference interval or survey-cycle weighting
is inferred.

This contract uses measurement dimensions informed by the
[LOINC Users' Guide](https://loinc.org/kb/users-guide) and case-sensitive unit
codes informed by [UCUM](https://ucum.org/ucum). It is **not** a LOINC validator,
a complete UCUM parser, or a claim of certified UCUM conformance. Pint supplies
ordinary dimensional scaling; no custom unit expressions or executable conversion
code can be supplied by a profile. See
[Pint's dimensional contexts](https://pint.readthedocs.io/en/stable/user/contexts.html).

## Source Declaration

Each source must have a distinct SOURCE.ID, a SITE_ID and a RECORD_ID bound to a
stable COLUMN.ID. The same institution may supply multiple exports. Local record
IDs may repeat; the output key is (source_id, source_row), not a patient identity.
All mapped results require an explicit UNIT and MEASUREMENT. For example:

```toml
[SOURCE]
ID = "site_a_export_01"
SITE_ID = "site_a"
RECORD_ID = "accession_id"

[COLUMN.accession]
ID = "accession_id"

[COLUMN.local_creatinine]
ID = "local_cr"
UNIT = "mg/dL"

[COLUMN.local_creatinine.MEASUREMENT]
COMPONENT = "creatinine"
SPECIMEN = "serum"
METHOD = "declared-enzymatic-protocol-v1"
TIME = "Pt"
SCALE = "Qn"
PROPERTY = "mass_concentration"
DEVICE = "declared-platform"
CALIBRATION = "declared-traceability-v1"
```

The identifiers above are template declarations, not verified clinical mappings.
Column headers additionally declare NUM or <NUM> and normally RESULT. AGE and
categorical results are not concentration inputs. Source columns and their
ColumnSpecs must agree and have unique names; mapped IDs must resolve uniquely.
Every RESULT column must be mapped or explicitly listed in IGNORE_IDS. Unmapped
columns remain in the preserved source payload, not in the canonical analysis.

Required MEASUREMENT keys are COMPONENT, SPECIMEN, METHOD, TIME, SCALE and PROPERTY.
Optional DEVICE and CALIBRATION are exact-match constraints when either side
declares them. Unknown keys are rejected. Except for PROPERTY in an explicitly
declared mass/substance bridge, all context values must equal the target values.
SCALE must be Qn. Unknown source method/specimen must not be filled with the target
values merely to make the check pass. Optional omissions do not prove equivalence.

## Explicit Profile

Supply a TOML profile to `tametools integrate`, or embed the same table under
META.INTEGRATION_PROFILE. If an input already embeds a profile, its hash must
match the requested one. Profile and nested structural keys shown here are
uppercase and unknown keys are errors.

```toml
VERSION = 1
ID = "creatinine-unit-plan-v1"
REFERENCE = "Reviewed mapping plan; replace with your versioned evidence"

[TARGETS.creatinine]
UNIT = "umol/L"
[TARGETS.creatinine.MEASUREMENT]
COMPONENT = "creatinine"
SPECIMEN = "serum"
METHOD = "declared-enzymatic-protocol-v1"
TIME = "Pt"
SCALE = "Qn"
PROPERTY = "substance_concentration"
DEVICE = "declared-platform"
CALIBRATION = "declared-traceability-v1"

[SOURCES.site_a_export_01]
MAPPINGS = [{ COLUMN_ID = "local_cr", TARGET_ID = "creatinine", BRIDGE = "cr_mass_to_molar" }]

[SOURCES.site_b_export_01]
MAPPINGS = [{ COLUMN_ID = "cr_si", TARGET_ID = "creatinine" }]
# IGNORE_IDS = ["result_intentionally_excluded"]

[BRIDGES.cr_mass_to_molar]
COMPONENT = "creatinine"
FROM_UNIT = "mg/dL"
TO_UNIT = "umol/L"
FACTOR = "88.4"
REFERENCE = "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/BIOPRO_J.htm"
```

For site B, declare the corresponding source ID, cr_si ID, umol/L unit and
substance_concentration property in its own input. Other measurement context must
match. The factor above is the NHANES codebook's representation factor, not a
universal cross-assay calibration claim. Profiles may declare sources not supplied
in a particular invocation. Each supplied source needs a mapping; at least one
source, target and mapping per source are required. Target IDs match
`[A-Za-z][A-Za-z0-9_]{0,63}`; generated names must not collide. A local column and a
target may each appear only once per source mapping.

## Supported Units and Numbers

| PROPERTY | Exact supported unit codes |
|---|---|
| mass_concentration | g/L, g/dL, mg/L, mg/dL, ug/L, ug/dL, ug/mL, ng/mL, pg/mL |
| substance_concentration | mol/L, mmol/L, umol/L, nmol/L, pmol/L |
| catalytic_activity_concentration | U/L, kat/L, ukat/L |

Pint 0.24.4 supplies same-property conversion factors. Mass/substance conversion
requires a positive, finite, analyte-specific directed BRIDGE with an explicit
reference. The implementation does not infer molecular weight, invert a bridge,
accept affine method corrections, or transform a different component. U/L denotes
enzyme catalytic activity in this bounded vocabulary; IU/L is not an alias.

Unlisted units, including %, mmol/mol, mEq/L, IU/L and temperature offsets, are
rejected. In particular, HbA1c method scales are not treated as generic unit
rescaling. Display spellings such as the micro symbol, uppercase MG/DL or mg%
require an externally reviewed mapping; the engine does not guess aliases or
extract units from values such as `1 mg/dL`.

Only finite decimal/scientific numeric strings are accepted. Commas, ranges,
boolean values and textual results are errors. <NUM> additionally accepts
`<`, `<=`, `>`, `>=`, and `=`. A positive factor preserves the comparator and
scales its threshold. Non-VALUE TAME states (ABSENT, NULL, EMPTY and WS) are
retained, never silently changed to zero. Numeric rendering uses Decimal with
80-digit multiplication precision and fixed-point output; it is not an assertion
of additional measurement precision. Numeric text is bounded to 400 characters
and adjusted decimal exponents within +/-300, including the converted value.

## Limits and Reference Intervals

A source result may declare constant LLOD/ULOQ or LLOD_ID/ULOQ_ID, but not both for
the same bound. Referenced columns require NUM and the same UNIT as the source
result. Positive bounds are required when present. Limits remain row-specific.

REFERENCE_INTERVAL accepts LOW/HIGH or LOW_ID/HIGH_ID and optional POPULATION and
REFERENCE. Its numbers use the source result unit; referenced columns must declare
that unit. Missing bounds remain missing, and lower must not exceed upper when
both exist. Source population/evidence stays in the source catalogue. No healthy
population or common reference interval is inferred and no clinical abnormality
flag is recomputed. Per-row age/sex-specific bounds must already be resolved in
the input; demographic interpretation is not guessed.

An existing CENSORING binding is verified before conversion. Its fixed LIMIT
becomes the row LLOD, and a separately declared LLOD must agree. Original released
fill values and flags remain in the source payload. The canonical result stores
the scaled comparator and row threshold, not an invalid inherited global
CENSORING binding. Variable-limit survey estimation remains unsupported.

## Output and Validation

The wide output includes five provenance columns: source_id, site_id, source_row
(1-based original row), source_record_id and source_row_json. Every target has:

| Column | Meaning |
|---|---|
| target | Canonical result, declared target UNIT and MEASUREMENT; RESULT::<NUM> |
| target__raw | Source result text/state; ORIGINAL and STR, not pooled as a number |
| target__raw_unit | Original unit code for that row |
| target__llod / target__uloq | Row limits in the target unit |
| target__ref_low / target__ref_high | Source-specific reference bounds in the target unit |

All result/auxiliary columns allow retained nonvalue states. A source with no
mapping to a target contributes ABSENT cells to that target. There is no filling
from a different source column or an alternative native/SI representation.

INTEGRATION_RESULT holds VERSION=1, the full PROFILE, PROFILE_SHA256, EXPECTED_ROWS,
KEY_FIELDS, the engine version and a scope statement. SOURCE_CATALOG stores each
source's ID/site relationship, row count, serialized cell checksum, original
ColumnSpecs and META/SCHEMA/JOB with their control checksum. Hashes are full
SHA-256 over deterministic JSON (ASCII escaping, sorted keys, no NaN, compact
separators, `<` escaped as `\u003c`). Controls must be JSON-serializable; express
dates in preserved metadata as strings. Source-row JSON retains all original
column cell representations, including unmapped values. This increases file size.

`restore_integration_sources` reconstructs original TAME cell text/states, headers,
row order and metadata, not byte-identical source files or original Python scalar
types. It verifies row keys and checksums. `validate_dataset` additionally
recomputes the integration from the originals/profile and compares canonical
values, bounds, raw values, column units/context and row provenance. Reordering
rows or changing display headers while retaining ID bindings is supported.
Filtering rows, replacing values or altering context without regenerating the
integration invalidates the result. To change the plan, restore the originals
and integrate again. Both reintegration of an integrated input and ordinary merge
of integrated outputs are rejected.

Checksums detect inconsistency relative to the embedded record. They are not
digital signatures, external authentication, or anonymization. Original record
identifiers and all other input columns remain present. Remove identifying fields
under the applicable governance process **before** integration if appropriate.

## Execution and Storage

Install the optional extra from this source tree: `pip install './tametools[integration]'`.

```bash
tametools integrate a.tame b.xlsx --profile reviewed.toml --preview
tametools integrate a.tame b.xlsx --profile reviewed.toml --output integrated.tame
tametools validate integrated.tame
```

Preview performs the validation/conversion without saving and shows source/target
units, factor, reference, row count and nonvalue-state count. `INTEGRATE` is also
a workflow step for one source with an embedded profile. Multi-source collection
is explicit through the CLI/API. Output receives fresh analysis metadata and
CRR=DELETE by default; a source's workflows, policies or survey design are not
silently promoted to global settings. Censored thresholds are not true values.

Full .tame and .xlsx outputs preserve the contract. The integrate command rejects
data-only/meta-only and CSV outputs. Integrated XLSX data strings are explicitly
written as text, including `=...`, to avoid formula execution or cache loss.
Integrated provenance exceeding Excel's 32,767 UTF-16-unit cell limit is rejected
before writing; use .tame for such payloads. Reserved TAME section markers in
unescaped metadata or direct cells are rejected for .tame output; escaped
source-row JSON can preserve markers in unmapped data. Such exceptional direct
content may use .xlsx if within its limits.

## Unresolved Institutional Differences

Keep incompatible methods, specimens, platforms or calibrations in **separate
targets**, with explicit different context declarations, even if their units can
be scaled. Compare them by site/method rather than pooling them. Empirically
validated calibration equations would need a separately governed extension with
applicability ranges, uncertainty and validation evidence; this version does not
execute them. Patient linkage, site-specific categorical dictionaries, date/time
zones, specimen timing, reference-population reconciliation and longitudinal
deduplication also require separate reviewed processes. SURVEY-bearing inputs are
rejected, so complex-survey designs cannot be silently erased or concatenated.

The NHANES supplement tests two disjoint representations of one survey release,
not observed cross-institution assay equivalence. The
[BIOPRO_J codebook](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/BIOPRO_J.htm)
provides the native/SI factors used. The
[HSCRP_J codebook](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/HSCRP_J.htm)
provides the original detection-limit information. No clinical reference interval
is invented in these examples.
