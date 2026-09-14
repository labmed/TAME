# NHANES Public Data Download

Requesting additional
file IDs in an existing same-cycle cache verifies existing files and downloads the
missing pairs. Downloading itself does not join data or choose analysis weights.

```bash
tametools download nhanes --cycle 2017-2018 --files DEMO_J BIOPRO_J HSCRP_J --output-dir raw
tametools download nhanes --cycle 2017-2018 --files DEMO_J BIOPRO_J HSCRP_J --output-dir raw --offline
```

The command downloads explicitly named XPT and HTML codebook pairs from the
official CDC/NCHS HTTPS directory. File identifiers are uppercase and contain no
extension or path. Regular 1999-2000 through 2017-2018 releases and August
2021-August 2023 are supported; the irregular 2017-March 2020 prepandemic release
is intentionally not represented as an ordinary cycle. Registered URL patterns
do not guarantee that every file identifier exists. Missing files stop with an
error; a different cycle or file is never substituted automatically.

Each release has its own units, weights, eligibility criteria, detection limits,
and joining requirements. Consult the release-specific codebooks before analysis.
The web workbench's **public example loader** can download and prepare an NHANES
example; see the [web quickstart](WEB_QUICKSTART_KO.md).

## Files and Checks

`raw/sources/` contains unchanged response bytes. `raw/source_manifest.json`
records schema version 1, provider, cycle, original/resolved URLs, UTC retrieval
time, byte count and SHA-256 for each file. XPT envelopes and the matching
codebook's release heading/file identifier are checked before saving. These
checks detect common truncated downloads and HTML error responses; an XPT reader
and codebook validation remain necessary before using observations.

Existing tracked files are verified without a new network request. An existing
untracked file, corrupt cache, wrong cycle or symlinked source/manifest is not
silently overwritten. A missing cached file can be downloaded again only if it
matches the recorded hash and size. Completed files survive later network
failures, so repeating the download command resumes missing files. No `--force`
or silent refresh mode discards provenance. Use a new directory for a deliberately
different snapshot, review the difference and keep both manifests.

`--offline` makes no network requests and fails if a selected file is missing.
`--expected-manifest expected_sources.json` accepts an explicit filename-to-SHA256
JSON table, pinning every requested XPT and codebook, including cache hits. Locally recorded hashes detect
changes relative to that snapshot; they are not CDC-published digital signatures.

Downloads restrict redirects to the same official HTTPS release directory,
limit each response to 128 MiB and apply a per-request `--timeout` of 45 seconds
by default (allowed 1-300). Transient failures receive at most three attempts;
404 responses are not retried. Manifest updates are atomic and a directory lock
prevents cooperating writers from interleaving. If a process is forcibly killed,
first confirm that no downloader is running before removing a stale
`.nhanes-download.lock`. A lock is never automatically taken over.

## Scope and Terms

The downloader is a standard-library component; `tametools[nhanes]` adds the
ReadStat interface needed for XPT preparation. Download does not convert data,
select participants, infer survey weights, merge cycles or create an analysis
plan. Those are separate, explicit analysis decisions.

NCHS public-use data may be used only for statistical reporting and analysis.
Do not attempt identification or link to individually identifiable data. Cite
CDC/NCHS and the release codebooks, and follow the
[NCHS Data User Agreement](https://www.cdc.gov/nchs/policy/data-user-agreement.html).
Public access is separate from institutional ethics-review requirements.

See the [web quickstart](WEB_QUICKSTART_KO.md) for example loading.
