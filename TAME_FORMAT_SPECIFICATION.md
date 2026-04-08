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
- `META[WORKS]`
- `META[SUBWORKS]`
- `META[PIPELINES]`
- `META[FUNCTIONS.<name>]`
- `META[PLUGINS]`
- `META[USE]`

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
[[GENDER]]sex
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
"sex" = ["GENDER"]
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
- `HOSPITAL_ID`
- `AGE`
- `GENDER`
- `BY`
- `SHEET`
- `IMAGE`
- `REF_LOW`
- `REF_HIGH`
- `COMMENT`

### 11.2 Type and Storage Tags

- `NUM`
- `<NUM>`
- `STR`
- `TXT`
- `CAT`
- `B64`
- `PATH`

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
- a provenance column tagged as `[[SHEET::STR]]시트명` is added

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
[[ID::STR]]record_id	[[GENDER]]sex	[[AGE]]age	[[RESULT::<NUM>]]result
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
