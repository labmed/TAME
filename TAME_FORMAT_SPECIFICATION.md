# TAME Format Specification

## 1. Scope

This document defines the TAME file format as a sectioned text container for tagged tabular data and its companion storage forms.

## 2. File Classes

### 2.1 `.tame`

A full TAME dataset file.

- A canonical `.tame` file MUST contain `<META>` and `<DATA>`.
- A `.tame` file MAY also contain `<SCHEMA>` and `<JOB>`.
- A reader MUST require a `<DATA>` section when loading a dataset from `.tame`.

### 2.2 `.meta.tame`

A control-only sidecar file.

- A `.meta.tame` file MUST NOT contain `<DATA>`.
- A `.meta.tame` file MAY contain `<META>`, `<SCHEMA>`, and `<JOB>`.
- A `.meta.tame` file is intended to be paired with `.xlsx` or `.data.tame`.

### 2.3 `.data.tame`

A data-only sidecar file.

- A `.data.tame` file MUST contain `<DATA>`.
- A canonical `.data.tame` file SHOULD contain only `<DATA>`.
- A sibling file with the same stem and suffix `.meta.tame` MAY provide control sections.

## 3. Character Encoding and Container Syntax

- TAME text files MUST use UTF-8 encoding.
- Canonical writers SHOULD emit LF line endings.
- Section names are `SCHEMA`, `JOB`, `META`, and `DATA`.
- Readers MUST treat section tags case-insensitively.
- Writers SHOULD emit section tags in uppercase.
- Each section MUST appear at most once.
- Section bodies are delimited as:

```text
<SECTION_NAME>
...
</SECTION_NAME>
```

- Canonical section order is:

```text
<SCHEMA>
<JOB>
<META>
<DATA>
```

## 4. Section Semantics

### 4.1 `SCHEMA`

Optional TOML control section for reusable schema information.

Current portable use:

- `SCHEMA[column.<name>].tags = [...]`

### 4.2 `JOB`

Optional TOML control section for job payloads or reusable execution metadata.

### 4.3 `META`

TOML control section for dataset-level metadata.

Common namespaces used by the current format/toolchain are:

- `META[INFO]`
- `META[SETTINGS]`
- `META[TAGS]`
- `META[DATA]`
- `META[CATEGORIES.<vocab>]`
- `META[DATETIME]`
- `META[WORKS]`
- `META[SUBWORKS]`
- `META[PIPELINES]`
- `META[FUNCTIONS.<name>]`
- `META[PLUGINS]`
- `META[USE]`
- `META[[LOG]]`
- `META[INTEGRITY]`

Unknown keys MAY be present and MUST be preserved as TOML content by format-aware tooling when practical.

### 4.4 `DATA`

Tab-separated data table.

- Canonical files MUST use tab (`\t`) as the delimiter.
- Canonical files SHOULD use a single header row.
- Canonical writers MUST emit tagged headers in the first row.

## 5. Control Section Format

- `META`, `SCHEMA`, and `JOB` bodies MUST be valid TOML documents when present.
- Empty control sections SHOULD be omitted rather than emitted as empty tags.

## 6. Header Syntax

### 6.1 Canonical Header Form

The canonical tagged-header syntax is:

```text
[[TAG]]display_name
[[TAG::TAG]]display_name
[[TAG::TAG::TAG]]display_name
```

Examples:

```text
[[RESULT::NUM]]result
[[RESULT::<NUM>]]reported_value
[[AGE]]age
[[SEX]]sex
[[HOSPITAL_ID]]hospital_code
[[IMAGE::B64]]thumbnail
```

### 6.2 Parsing Rules

- The tag block begins with `[[` and ends at the first following `]]`.
- The text after `]]` is the display name.
- A header without a tag block is a plain display name with no header tags.
- Tag tokens are separated by `::`.
- Tag tokens MAY contain ASCII letters, digits, underscore, and the characters `<`, `>`, `(`, `)`, `%`.
- Tag tokens MUST be normalized to uppercase by readers, except that function-style suffixes such as `DATE(%y%m%d)` preserve the parenthesized suffix text.

## 7. Effective Tag Resolution

The effective tag set for a column is the ordered union of:

1. header tags
2. `META[TAGS][display_name]`
3. `SCHEMA[column.<display_name>].tags`

Duplicates MUST be removed while preserving first occurrence order.

Example:

```toml
[TAGS]
"result" = ["RESULT", "NUM"]

[column.result]
tags = ["BY"]
```

If the header is `[[<NUM>]]result`, the effective tags are:

```text
<NUM>, RESULT, NUM, BY
```

## 8. `META[TAGS]` Mapping

`META[TAGS]` maps display names to tag arrays.

Example:

```toml
[TAGS]
"result" = ["RESULT", "NUM"]
"sex" = ["SEX"]
"age" = ["AGE"]
```

When a `.meta.tame` sidecar is materialized from an existing dataset, writers MAY populate `META[TAGS]` with the effective tags of each column so the tags can be reattached to a fresh `.xlsx` or `.data.tame`.

## 9. Data Layout Metadata

`META[DATA]` MAY provide layout hints.

Current field:

- `headers = <int>`

Rules:

- The default header-row count is `1`.
- If `headers > 1`, readers MAY combine the first `N` rows into a single logical header using `__` between non-empty header fragments.
- Canonical TAME writers SHOULD emit one header row regardless of input compatibility mode.

## 10. Cell-State Serialization

### 10.1 Logical States

TAME distinguishes five logical cell states:

- `ABSENT`
- `NULL`
- `EMPTY`
- `WS`
- `VALUE`

### 10.2 Default Serialized Tokens

The canonical default tokens are:

- `<<ABSENT>>`
- `<<NULL>>`
- `<<EMPTY>>`
- `<<WS:n>>`

`n` is the number of whitespace characters.

### 10.3 Semantics

- `ABSENT` represents a missing cell value and round-trips to `None`.
- `NULL` represents an explicit null sentinel.
- `EMPTY` represents a zero-length string.
- `WS` represents a string consisting only of whitespace.
- `VALUE` represents any other cell value.

### 10.4 Escaping Reserved Tokens

If a literal string equals a reserved token, it MUST be escaped with the default prefix `\`.

Examples:

```text
\<<NULL>>
\<<EMPTY>>
```

### 10.5 Token Configuration

`META[SETTINGS]` MAY override token serialization keys:

- `ABSENT_TOKEN`
- `NULL_TOKEN`
- `EMPTY_TOKEN`
- `WS_TOKEN_PREFIX`
- `TOKEN_SUFFIX`
- `ESCAPE_PREFIX`

Readers and writers using custom tokens MUST apply the same configuration to preserve round-trip behavior.

## 11. Common Semantic and Type Tags

The format does not limit tags to a fixed vocabulary, but the following tags are standardized by current usage.

### 11.1 Semantic Tags

- `RESULT`
- `TESTNAME`
- `ITEM`
- `ID`
- `NAME`
- `HOSPITAL_ID`
- `AGE`
- `SEX`
- `BY`
- `SHEET`
- `IMAGE`
- `REF_LOW`
- `REF_HIGH`
- `COMMENT`

`SEX` is the canonical sex tag (canonical values `male`, `female`, `other`, `unknown`).

### 11.2 Type and Storage Tags

- `NUM` — strict numeric
- `<NUM>` — comparator numeric (may include `<`, `<=`, `>`, `>=`, `=`)
- `STR` — string / identifier (preserves leading zeros)
- `CATEGORY` — categorical column (short alias: `CAT`)
- `DATE` — calendar date, normalized to ISO 8601 `YYYY-MM-DD`
- `DATETIME` — date and time, normalized to ISO 8601 `YYYY-MM-DDTHH:MM:SS`
- `TIME` — time of day, normalized to `HH:MM:SS`
- `B64` — Base64-embedded binary (with `IMAGE`)
- `PATH` — external file path (with `IMAGE`)

### 11.3 Examples

- `[[RESULT::NUM]]result`
- `[[RESULT::<NUM>]]result`
- `[[ID::STR]]record_id`
- `[[IMAGE::PATH]]image_path`
- `[[SHEET::STR]]sheet_name`

## 12. Metadata Namespaces Used by Current TAME Files

### 12.1 `META[SETTINGS]`

Format-relevant settings MAY include:

- cell-state token overrides
- comparator policy keys such as `CRR`
- validation policy keys such as `VALIDATE_ERROR`

### 12.2 `META[WORKS]` and `META[SUBWORKS]`

Named step pipelines stored as TOML arrays.

Example:

```toml
[WORKS]
DEFAULT = ["VALIDATE", "DESCRIBE"]

[SUBWORKS]
PREP = ["VALIDATE", "SPLIT_COMPARATOR"]
```

### 12.3 `META[PIPELINES]`

Named command pipelines stored as arrays of command strings.

Example:

```toml
[PIPELINES]
DEFAULT = [
  "sample --rows 100 --seed 7",
  "export outputs/sample.sql --format sql",
]
```

### 12.4 `META[FUNCTIONS.<name>]`

Embedded function definitions.

Current fields:

- `LANG`
- `ENTRY`
- `SOURCE`
- optional `KIND`

Example:

```toml
[FUNCTIONS.trim_result]
LANG = "python"
ENTRY = "run"
SOURCE = '''
def run(dataset, options, api):
    return dataset
'''
```

### 12.5 `META[PLUGINS]` and `META[USE]`

Optional external module registration.

Examples:

```toml
[PLUGINS]
MODULES = ["tutorial.plugin_examples.example_count_plugin"]

[USE]
DEFAULT = ["package.module"]
```

### 12.6 `META[CATEGORIES.<vocab>]`

User-defined category vocabularies that normalize institution-specific spellings to canonical values. A categorical column references a vocabulary with a `CATEGORY` tag plus the vocabulary name, e.g. `[[CATEGORY::RESULT_QUAL]]판정`.

Fields:

- `VALUES` — allowed canonical values
- `MAP` — synonym-to-canonical mapping (matched case-insensitively, ignoring surrounding whitespace)
- `STRICT` — if `true`, values that do not resolve to an allowed value are reported by `validate`

```toml
[CATEGORIES.RESULT_QUAL]
VALUES = ["POSITIVE", "NEGATIVE", "EQUIVOCAL"]
MAP = { "양성" = "POSITIVE", "+" = "POSITIVE", "음성" = "NEGATIVE", "-" = "NEGATIVE" }
STRICT = true
```

`normalize-categories` rewrites matched cells to their canonical values.

### 12.7 `META[DATETIME]`

Optional input formats used when normalizing `DATE`, `DATETIME`, and `TIME` columns to ISO 8601. If absent, formats are inferred, and Excel serial numbers are recognized.

```toml
[DATETIME]
FORMATS = ["%Y/%m/%d %H:%M", "%d-%b-%Y"]
```

`normalize-datetimes` rewrites parseable values; unparseable values are preserved and reported.

### 12.8 `META[[LOG]]` (Provenance)

An array of log-entry tables recording how the artifact was produced. Each pipeline step, action, or CLI command appends an entry; chaining preserves a parent artifact's log.

Entry fields:

- `TIMESTAMP` — ISO 8601 timestamp
- `OPERATION` — operation name
- `TOOL` — producing tool
- `PARENT` — parent artifact (optional)
- `[LOG.PARAMS]` — operation parameters (optional)

```toml
[[LOG]]
TIMESTAMP = "2026-06-27T00:00:00+00:00"
OPERATION = "CLI:RUN-PLUGIN"
TOOL = "tametools"

[LOG.PARAMS]
command = "tametools run-plugin data.tame REFERENCE_INTERVAL --output ri.tame"
```

`tametools logs FILE` lists the log; `tametools verify FILE` re-runs the declared pipeline and checks that the content hash matches.

### 12.9 `META[INTEGRITY]` (Tamper-evidence)

A content-hash stamp for tamper detection.

- `CONTENT_HASH` — deterministic hash over tagged headers and data (excludes `META`, so timestamps do not affect it)
- `ALGO` — hash-algorithm identifier

```toml
[INTEGRITY]
CONTENT_HASH = "a70ddddbeaac578f"
ALGO = "sha256-16"
```

`tametools stamp` writes the stamp; `tametools check-integrity` recomputes and compares, detecting any modified cell.

## 13. `.xlsx` Mapping

### 13.1 Control Sheets

When represented as `.xlsx`:

- sheet `META` maps to the `META` section
- sheet `SCHEMA` maps to the `SCHEMA` section
- sheet `JOB` maps to the `JOB` section

Each control sheet stores the TOML document as plain text in the first column, one line per row.

### 13.2 Data Sheets

Data-sheet discovery follows these rules:

1. If sheets named `DATA`, `DATA_1`, `DATA_2`, ... are present, they are the data sheets in numeric order.
2. Otherwise, every non-control sheet is treated as a data sheet.

### 13.3 Multi-Sheet Flattening

When multiple data sheets are loaded into one dataset:

- rows from all sheets are combined into a single `DATA` table
- a provenance column tagged as `[[SHEET::STR]]sheet_name` is added

### 13.4 Multi-Sheet Restoration

When writing `.xlsx` from a dataset:

- if no column has the `SHEET` tag, a single data sheet named `DATA` is written
- if a column has the `SHEET` tag, rows are grouped by that column and written to separate sheets

## 14. Sidecar Resolution

The following sidecar conventions are standardized:

- `example.xlsx` MAY be paired with `example.meta.tame`
- `example.data.tame` MAY be paired with `example.meta.tame`

When both are present, the control bundle from `.meta.tame` augments the data-bearing file.

## 15. Canonical Full Example

```text
<META>
[INFO]
NAME = "example"

[TAGS]
"result" = ["RESULT", "<NUM>"]
</META>
<DATA>
[[ID::STR]]record_id	[[SEX]]sex	[[AGE]]age	[[RESULT::<NUM>]]result
P001	F	32	<3
</DATA>
```

## 16. Canonical Sidecar Example

`example.meta.tame`

```text
<META>
[INFO]
NAME = "example"

[TAGS]
"result" = ["RESULT", "<NUM>"]
</META>
```

`example.data.tame`

```text
<DATA>
record_id	sex	age	result
P001	F	32	<3
</DATA>
```

## 17. tametools 0.4.0 workflow extensions

The following application-level declarations complement this container specification:

- [Analysis plans](docs/TAME_ANALYSIS_PLAN_V1.md)
- [Measurement context and units](docs/TAME_MEASUREMENT_TAGS.md)
- [Source-specific category and age declarations](docs/INIT_SEMANTICS_KO.md)
- [Processing history](docs/ANALYSIS_LOG_GUIDE_KO.md)
- [Reference-interval settings](docs/REFERENCE_INTERVAL_EP28_GUIDE_KO.md)
