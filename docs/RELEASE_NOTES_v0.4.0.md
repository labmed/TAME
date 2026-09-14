# tametools 0.4.0

- Review complete columns during `init`, with source-specific sex mappings, optional completed-year ages and age groups, and explicit unit conversion.
- Run `RI_EP28` with nonparametric, parametric, robust, and transformed estimates; configurable outlier handling, partitions, LAVE, interval verification, and reports.
- Use one browser workbench for local files and optional public examples. Preserve the selected source during chained analyses and inspect results and processing history.
- Store effective parameters, parent history, input hashes, and processing results in TAME outputs.
- Distribute a Python wheel/source package, a local Chrome bundle, and Windows portable/MSI builds from the same source.

The browser bundle requires a supported 64-bit Python installation and internet access for initial dependency setup. Windows EXE/MSI bundles include their Python runtime. Reference-interval outputs require review of reference individuals, sampling and measurement context before clinical use.

See `BUILD_VERIFICATION.json` and `SHA256SUMS.txt` in the release assets for the checks performed and file integrity.
