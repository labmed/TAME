# TAME and tametools

TAME is a tag-first text format for laboratory tabular data, and `tametools` is the reference toolkit that reads, validates, transforms, and analyzes that format.

## What This Repository Contains

- `TAME_FORMAT_SPECIFICATION.md`: formal file-format specification for TAME
- `tametools/`: installable Python package and CLI
- `tutorial/`: runnable examples for validation, anonymization, merge, export, plugins, embedded functions, and command pipelines
- `docs/`: implementation review and manuscript-related supporting documents

## Core Ideas

- Humans read column labels.
- `tametools` reads tags.
- Tagged headers such as `[[RESULT::NUM]]result`, `[[AGE]]age`, and `[[GENDER]]sex` drive validation and analysis.
- The canonical header syntax is `[[TAG::TAG]]display_name`.
- Cell states distinguish `ABSENT`, `NULL`, `EMPTY`, and whitespace-only values.
- Multi-hospital data can be merged by tag semantics instead of literal column names.
- `.tame`, `.xlsx`, `.meta.tame`, and `.data.tame` can be chained in repeatable workflows.

## Main Features

- Validation and descriptive analysis based on tagged headers
- Comparator-aware numeric handling for values such as `<3` or `>5000`
- Tag-based merge across differently named source columns
- Anonymization of `ID` and `HOSPITAL_ID` fields with mapping-table export
- Multi-sheet spreadsheet flattening and restoration
- Image-column conversion between `IMAGE::B64` and `IMAGE::PATH`
- Export to `csv`, `tsv`, `jsonl`, `sql`, `parquet`, `feather`, and `r_bundle`
- Reusable sidecar metadata with `.meta.tame`
- Template-based `import-xlsx` for replacing data while preserving tags and pipeline definitions
- Embedded `META[FUNCTIONS]` support for lightweight Python or R UDFs
- Embedded `META[PIPELINES]` support for chaining multiple `tametools` commands

## Installation

```bash
python3 -m pip install -e ./tametools
```

With Parquet export support:

```bash
python3 -m pip install -e './tametools[parquet]'
```

Without installation:

```bash
PYTHONPATH=tametools/src:. python3 -m tametools info tutorial/01_tagged_eda/sample_eda.tame
```

## Quick Start

Show CLI help:

```bash
PYTHONPATH=tametools/src:. python3 -m tametools --help
PYTHONPATH=tametools/src:. python3 -m tametools help run
```

Inspect a tagged dataset:

```bash
PYTHONPATH=tametools/src:. python3 -m tametools info tutorial/01_tagged_eda/sample_eda.tame
PYTHONPATH=tametools/src:. python3 -m tametools columns tutorial/01_tagged_eda/sample_eda.tame
PYTHONPATH=tametools/src:. python3 -m tametools validate tutorial/01_tagged_eda/sample_eda.tame
```

Run a metadata-defined workflow:

```bash
PYTHONPATH=tametools/src:. python3 -m tametools run tutorial/01_tagged_eda/sample_eda.tame DEFAULT
```

Reuse metadata on a new spreadsheet:

```bash
PYTHONPATH=tametools/src:. python3 -m tametools split-tame tutorial/01_tagged_eda/sample_eda.tame \
  --meta-output sample.meta.tame

PYTHONPATH=tametools/src:. python3 -m tametools run new_results.xlsx DEFAULT \
  --meta sample.meta.tame \
  --output validated.tame
```

Run an embedded command pipeline:

```bash
PYTHONPATH=tametools/src:. python3 -m tametools pipelines tutorial/11_command_pipelines/sample_command_pipeline.tame
PYTHONPATH=tametools/src:. python3 -m tametools run-pipeline tutorial/11_command_pipelines/sample_command_pipeline.tame DEFAULT
```

## Tutorials

Start here:

- [tutorial/README.md](tutorial/README.md)

## Documentation

- [TAME_FORMAT_SPECIFICATION.md](TAME_FORMAT_SPECIFICATION.md)
- [docs/TAME_SPEC_AND_TAMETOOLS_REVIEW.md](docs/TAME_SPEC_AND_TAMETOOLS_REVIEW.md)

## Data Safety Note

- The tutorial datasets are synthetic data.
