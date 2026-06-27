# TAME and tametools

TAME is a tag-first text format for laboratory tabular data, and `tametools` is the reference toolkit that reads, validates, transforms, and analyzes that format.

## What This Repository Contains

- `TAME_FORMAT_SPECIFICATION.md`: formal file-format specification for TAME
- `tametools/`: installable Python package and CLI
- `tutorial/`: runnable examples for validation, anonymization, merge, export, plugins, embedded functions, and command pipelines

## Core Ideas

- Humans read column labels.
- `tametools` reads tags.
- Tagged headers such as `[[RESULT::NUM]]result`, `[[AGE]]age`, and `[[SEX]]sex` drive validation and analysis.
- The canonical header syntax is `[[TAG::TAG]]display_name`.
- Cell states distinguish `ABSENT`, `NULL`, `EMPTY`, and whitespace-only values.
- Multi-hospital data can be merged by tag semantics instead of literal column names.
- Every operation appends provenance to `META[[LOG]]`, so a result `.tame` records how it was produced.
- `.tame`, `.xlsx`, `.meta.tame`, and `.data.tame` can be chained in repeatable workflows.

## Main Features

- `init`: bootstrap a starter `.tame` from any `.xlsx`/`.csv` by inferring header tags and datatypes
- Validation and descriptive analysis based on tagged headers
- Comparator-aware numeric handling for values such as `<3` or `>5000`
- Category vocabulary normalization (`normalize-categories`) and ISO 8601 date/time normalization (`normalize-datetimes`)
- Tag-based merge across differently named source columns
- Anonymization of `ID` and `HOSPITAL_ID` fields with mapping-table export
- Provenance and integrity: `logs` (processing history), `verify` (re-run hash match), `stamp`/`check-integrity` (tamper-evident), `check-excel` (spreadsheet coercion damage)
- Multi-sheet spreadsheet flattening and restoration
- Image-column conversion between `IMAGE::B64` and `IMAGE::PATH`
- Export to `csv`, `tsv`, `jsonl`, `sql`, `parquet`, `feather`, and `r_bundle`
- Reusable sidecar metadata with `.meta.tame`
- Template-based `import-xlsx` for replacing data while preserving tags and pipeline definitions
- Embedded `META[FUNCTIONS]` (lightweight Python/R UDFs) and `META[PIPELINES]` (chained commands)

## Installation

### Windows (recommended) — installer

Download `tametools-0.2.0-x64.msi` from the [latest release](https://github.com/labmed/TAME/releases/latest)
and run it. The `tametools` command is added to `PATH` and a **tametools Web Workbench** shortcut is created.
A closed-network offline bundle (`tametools-windows-offline.zip`) is also provided.

### pip (Linux/macOS/Windows)

```bash
pip install 'tametools[report,web]'
```

Or install the Python wheel attached to the [latest release](https://github.com/labmed/TAME/releases/latest):

```bash
pip install ./tametools-0.2.0-py3-none-any.whl
```

From a checkout of this repository:

```bash
pip install -e './tametools[report,web]'
```

## Quick Start

All examples below use the installed `tametools` command.

```bash
tametools --help
tametools doctor                                   # check install and optional dependencies
```

Bootstrap a `.tame` from any spreadsheet (new users start here):

```bash
tametools init data.xlsx --output data.tame        # infer header tags and datatypes
```

Inspect, validate, and analyze a tagged dataset:

```bash
tametools info tutorial/01_tagged_eda/sample_eda.tame
tametools columns tutorial/01_tagged_eda/sample_eda.tame
tametools validate tutorial/01_tagged_eda/sample_eda.tame
tametools describe tutorial/01_tagged_eda/sample_eda.tame
```

Run a metadata-defined workflow and inspect its provenance:

```bash
tametools run tutorial/01_tagged_eda/sample_eda.tame DEFAULT --output out.tame
tametools logs out.tame                            # processing history embedded in the artifact
tametools verify tutorial/01_tagged_eda/sample_eda.tame
```

Reuse metadata on a new spreadsheet:

```bash
tametools split-tame tutorial/01_tagged_eda/sample_eda.tame --meta-output sample.meta.tame
tametools run new_results.xlsx DEFAULT --meta sample.meta.tame --output validated.tame
```

> Without installing, prefix any command with `PYTHONPATH=tametools/src:. python3 -m` from a repository checkout,
> e.g. `PYTHONPATH=tametools/src:. python3 -m tametools info tutorial/01_tagged_eda/sample_eda.tame`.

## Tutorials

Start here:

- [tutorial/README.md](tutorial/README.md)

## Documentation

- [TAME_FORMAT_SPECIFICATION.md](TAME_FORMAT_SPECIFICATION.md)

## Releases

Windows installer (MSI), offline bundle, Python wheel, and source distribution are published on the
[Releases page](https://github.com/labmed/TAME/releases).

## Data Safety Note

- The tutorial datasets are synthetic data.
