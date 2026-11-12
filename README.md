# TAME and tametools 0.5.0

TAME stores tabular data, column semantics, analysis settings, and processing history in one UTF-8 text file. **tametools** provides a Python API, command-line tools, and a local browser workbench.

## Choose how to run

| Distribution | Requirements | Start |
|---|---|---|
| Windows installer | Windows x64 | Install `tametools-0.5.0-x64.msi`, then open **tametools Web Workbench** |
| Windows portable | Windows x64 | Extract the complete `tametools-0.5.0-windows-x64-portable.zip`; run `tametools/tametools-web.exe` |
| Local Chrome bundle | Python 3.10–3.13, 64-bit; Chrome; internet for first setup | Extract `tametools-0.5.0-browser-local.zip`; run `Start_Chrome.cmd` or `bash Start_Chrome.sh` |
| Python package | Python 3.10+ | Install the wheel or the source package |

Download files from [GitHub Releases](https://github.com/labmed/TAME/releases). The browser bundle runs a Python server on your own computer at `127.0.0.1:8765`; it is not a serverless HTML application. Node.js is unnecessary when using the prebuilt browser bundle.

[한국어 브라우저 실행 안내](README_BROWSER_KO.md) · [웹 사용 안내](docs/WEB_QUICKSTART_KO.md)

## Install from source

```sh
python -m pip install './tametools[analysis,nhanes,integration,parquet,web]'
tametools --version
tametools doctor
tametools plugins
```

The wheel contains the CLI and Python library. The web backend and frontend are provided in this repository and in the browser/Windows bundles.

## Start a data workflow

```sh
tametools init data.csv --output starter.tame
tametools init legacy.csv --encoding cp949 --output starter.tame   # a table that is not UTF-8: name its encoding
tametools init data.csv --output reviewed.tame --definitions starter.definitions.toml --require-reviewed
tametools validate reviewed.tame
tametools describe reviewed.tame
tametools convert reviewed.tame result.xlsx
```

`init` checks complete columns, proposes tags, and flags ambiguous sex codes, units, censoring policies, and missing-value meanings. Review the generated definitions before using them. Numeric sex codes can have different mappings for each source. Age review supports mixed month/year expressions, optional completed-year columns, and optional age groups.

Files are UTF-8. A `.tame` file in another encoding is refused with an explanation; `tametools validate old.tame --fix-encoding --from cp949 --output new.tame` writes a UTF-8 copy.

The bytes written to a pipe or a redirect are UTF-8 too (TAME text on `-` and `--json` output always, printed text by default). What a shell does with them is the shell's business: Windows PowerShell 5.1 reads piped output in the console code page and its `>` rewrites output as UTF-16. In that shell, pass an output file (`tametools convert in.tame out.tame`), and for pipelines either run `[Console]::OutputEncoding = [Text.Encoding]::UTF8` or let printed text follow the console code page with `tametools doctor --pipe-text console`. `tametools doctor` shows the setting in use. cmd.exe passes the bytes on unchanged.

[init and SEX/AGE declarations](docs/INIT_SEMANTICS_KO.md) · [Unit conversions](docs/INIT_UNIT_DECLARATIONS_KO.md)

## Features

- Typed headers and stable `COLUMN.ID` declarations, independent of display names.
- Explicit absent, null, empty, and whitespace states; comparator-aware values such as `<0.15`.
- Source-specific category normalization, declared unit conversion, merging, joins, and reshaping.
- Descriptive analysis, survey-aware summaries, anonymization, spreadsheet import/export, and image columns.
- `RI_EP28`: reference-interval estimation, outlier policies, partitions, interval verification, and reports.
- Per-file plugin selection in META, with dependency checks and a shared web/CLI execution policy.
- `QC_WORKFLOW`: connected preprocessing, QC, trends, run review, and shared HTML/Word reports.
- `WESTGARD_QC`: Westgard multirule against declared baselines, run decisions, action records, and an HTML report.
- `sync`: a project file that follows a growing source file and re-runs its work when data, settings, or plugin code change.
- Plugins use other plugins through one public module (`tametools.plugin_api`), with dependency and version checks.
- Reusable metadata and processing chains with settings, input hashes, and parent history.
- Browser workbench with example loading, source selection, result tables, and report downloads.

The included synthetic examples are software demonstrations. The Kenya example is derived from [CC0 public data](https://doi.org/10.5061/dryad.nvx0k6dns); its attribution is preserved in `web/backend/examples/README_KO.md`. NHANES examples are downloaded from CDC on request.

## Documentation

- [Choose plugins for a TAME file (한국어)](docs/PLUGIN_SELECTION_KO.md)

- [Release notes 0.5.0](docs/RELEASE_NOTES_v0.5.0.md)
- [File-format specification](TAME_FORMAT_SPECIFICATION.md)
- [META sections read and written by tametools (한국어)](docs/TAME_META_SECTIONS.md)
- [Tutorials](tutorial/README.md)
- [EP28 plugin](docs/REFERENCE_INTERVAL_EP28_GUIDE_KO.md)
- [Connected plugins and QC example (한국어)](docs/PLUGIN_WORKFLOWS_KO.md)
- [Plugin development](docs/PLUGIN_AUTHOR_GUIDE.md)
- [Analysis plans](docs/TAME_ANALYSIS_PLAN_V1.md)
- [Analysis history and verification](docs/ANALYSIS_LOG_GUIDE_KO.md)
- [Build and release](docs/BUILD_AND_RELEASE.md) · [GitHub 게시 안내](docs/GITHUB_UPLOAD_KO.md)

## Build the browser frontend

```sh
cd web/frontend
npm ci
npm run check
npm run build
cd ../..
python scripts/start_tametools_browser.py
```

## Tests

```sh
python -m pip install pytest httpx
python -m pytest tametools/tests web/backend/tests tests
```

Some optional integration tests require R or additional dependencies. The release verification records the tests actually run and any skips.
