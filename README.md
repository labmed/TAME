# TAME and tametools 0.4.0

TAME stores tabular data, column semantics, analysis settings, and processing history in one UTF-8 text file. **tametools** provides a Python API, command-line tools, and a local browser workbench.

## Choose how to run

| Distribution | Requirements | Start |
|---|---|---|
| Windows installer | Windows x64 | Install `tametools-0.4.0-x64.msi`, then open **tametools Web Workbench** |
| Windows portable | Windows x64 | Extract the complete `tametools-0.4.0-windows-x64-portable.zip`; run `tametools/tametools-web.exe` |
| Local Chrome bundle | Python 3.10–3.13, 64-bit; Chrome; internet for first setup | Extract `tametools-0.4.0-browser-local.zip`; run `Start_Chrome.cmd` or `bash Start_Chrome.sh` |
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
tametools init data.csv --output reviewed.tame --definitions starter.definitions.toml --require-reviewed
tametools validate reviewed.tame
tametools describe reviewed.tame
tametools convert reviewed.tame result.xlsx
```

`init` checks complete columns, proposes tags, and flags ambiguous sex codes, units, censoring policies, and missing-value meanings. Review the generated definitions before using them. Numeric sex codes can have different mappings for each source. Age review supports mixed month/year expressions, optional completed-year columns, and optional age groups.

[init and SEX/AGE declarations](docs/INIT_SEMANTICS_KO.md) · [Unit conversions](docs/INIT_UNIT_DECLARATIONS_KO.md)

## Features

- Typed headers and stable `COLUMN.ID` declarations, independent of display names.
- Explicit absent, null, empty, and whitespace states; comparator-aware values such as `<0.15`.
- Source-specific category normalization, declared unit conversion, merging, joins, and reshaping.
- Descriptive analysis, survey-aware summaries, anonymization, spreadsheet import/export, and image columns.
- `RI_EP28`: reference-interval estimation, outlier policies, partitions, interval verification, and reports.
- Reusable metadata and processing chains with settings, input hashes, and parent history.
- Browser workbench with example loading, source selection, result tables, and report downloads.

The included synthetic examples are software demonstrations. The Kenya example is derived from [CC0 public data](https://doi.org/10.5061/dryad.nvx0k6dns); its attribution is preserved in `web/backend/examples/README_KO.md`. NHANES examples are downloaded from CDC on request.

## Documentation

- [File-format specification](TAME_FORMAT_SPECIFICATION.md)
- [Tutorials](tutorial/README.md)
- [EP28 plugin](docs/REFERENCE_INTERVAL_EP28_GUIDE_KO.md)
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
