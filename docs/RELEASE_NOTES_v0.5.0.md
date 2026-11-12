# tametools 0.5.0

## What is new

- **Plugins that use other plugins.** `tametools.plugin_api` is the one module a plugin imports from
  (API version 2). A plugin can run another plugin, read its result, or import its functions; the run
  keeps the permissions it was started with, the log lists every plugin that took part, and
  `sync` watches the code of all of them. Dependencies may name a minimum version
  (`dependencies=("QC_ANALYSIS>=1.2",)`), and a plugin can declare the columns it returns.
- **Plugin questions, screens, commands and export formats.** A plugin can declare a form
  (`form=`), a result view (`register_view`), commands (`register_command`) and exporters
  (`register_exporter`).
- **`WESTGARD_QC`.** Westgard multirule against declared baselines, with run decisions, action
  records (`qc-action`, `qc-review`) and an HTML report.
- **`sync`.** A project file that follows a growing source file and re-runs its work when the
  data, the settings or the plugin code changed.

## Changes that can affect existing files and scripts

**File format**

- The section markers (`<META>`, `</DATA>`, …) are reserved in the whole file. Every read checks
  the container: a marker that appears twice, a section that is not closed, or text outside the
  sections is an error. A value that contains a marker is not written to `.tame` (use `.xlsx`).
  Before 0.5, such a value silently cut the file short when it was read back.
- The last cell of `DATA` keeps its trailing blanks.
- Escaping is symmetric: the text `\<<NULL>>` is written as `\\<<NULL>>` and read back unchanged.
  A file written before 0.5 from the text `\<<NULL>>` reads as `<<NULL>>`, as it did then.
- `.xlsx`: text that begins with `=` (for example the comparator result `=5`) is stored as text,
  not as a formula. A workbook whose formulas have no stored results is refused instead of being
  read as empty cells; open and save it in Excel, or replace the formulas by values.

**Text encoding**

- tametools reads and writes UTF-8 only. A `.tame`, `.csv` or `.tsv` file in another encoding is
  refused, with what its bytes look like (for example cp949 or UTF-16) and how to convert it.
  An encoding is never guessed.
- `tametools validate FILE --fix-encoding --from cp949 --output NEW.tame` writes a UTF-8 copy of a
  `.tame` file, records the conversion in its log, and validates the copy.
  `tametools init table.csv --encoding cp949` reads a table in a named encoding. A sync source that
  is not UTF-8 names its encoding in the project file: `[SYNC] ENCODING = "cp949"`.
- The bytes tametools writes to the standard streams follow the same rule. TAME text on `-` and
  `--json` output are always UTF-8. Printed text that is piped or redirected is UTF-8 by default,
  whatever the console code page; a program that read it in the console code page needs to read
  it as UTF-8.
- What a shell does with those bytes is outside this rule. Windows PowerShell 5.1 reads piped
  program output in the console code page, and its `>` rewrites the output as UTF-16. There,
  save `.tame` files with an output file argument (`tametools convert in.tame out.tame`), and for
  pipelines either run `[Console]::OutputEncoding = [Text.Encoding]::UTF8` in the session or
  let printed text follow the console code page: `tametools doctor --pipe-text console`
  (back with `--pipe-text utf-8`; for one session, set `TAMETOOLS_PIPE_TEXT`). With that setting,
  characters the code page lacks print as `?`. `tametools doctor` shows what applies.
  cmd.exe passes the bytes on unchanged. The setting takes `utf-8`, `console` or a code page by
  name such as `cp949`; anything that cannot carry printed text is refused, and such a value
  already in the settings file or the variable is passed over, so it can always be corrected.

**Quality control**

- A run is what one instrument measured together. The control lot is no longer part of the run,
  so levels with lot numbers of their own are compared by the within-run rules (`R_4s`, `2_2s`).
- A run identifier that appears with two different times on one instrument is an error. If run
  numbers start again every day, use an identifier that includes the date.
- `WESTGARD_QC` with `TRIGGER = "1_2S"`: the other rules are inspected when a control of the run
  is beyond 2 SD. Removing `1_2s` from the warnings, or making it a rejection rule, no longer
  switches the other rules off.
- "m of n" rules such as `2of3_2s` are flagged on the run that completes them, also at the
  start of a sequence and when a run has fewer than n controls.
- `QC_ANALYSIS` sigma: `TEA` and `BIAS` are both required, in percent, as one number, per level,
  or per analyte and level. A missing bias is no longer taken to be zero; the row says what is
  missing (`sigma_status`).

**Reference intervals**

- New work uses `RI_EP28`. The older `REFERENCE_INTERVAL` plugin reports the share of values
  outside a candidate interval; it gives a pass/fail verdict only from 20 values on, and that
  verdict is not the EP28 verification procedure.

**Command line and web**

- `tametools run` stops at a step that is neither a built-in step nor an available plugin, and
  exits with status 1.
- In `WORKS` and `PIPELINES`, a plugin that returns only a table hands that table to the next step.
- Plugin names that tametools uses itself (`INFO`, `COLUMN`, `VALIDATE`, …) cannot be registered.
- The web workbench decides where reports are written. `OUTPUT_DIR` and `REPORT_PATH` carried by
  an opened file are ignored, and such a path in a request is refused.
- The analysis input saved with a reference-interval report holds the settings that were applied
  in one section, so running that file again gives the result the report shows.
- A workbook is released as soon as it has been read, including the sheet preview, so it can be
  replaced or removed while the program is still running.

## Downloads

Windows installer, portable Windows app, local browser bundle and Python packages are attached to
the release when they have been built from this version. See `SHA256SUMS.txt` for file checksums
and `BUILD_VERIFICATION.json` for build verification.
