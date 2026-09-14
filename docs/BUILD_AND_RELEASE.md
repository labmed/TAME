# Build and release tametools 0.4.0

## Python library and CLI

```sh
python -m pip install build
python -m build --outdir dist/0.4.0 tametools
```

## Browser UI

```sh
cd web/frontend
npm ci
npm run check
npm run build
cd ../..
```

The browser archive contains this frontend build, the Python backend, and an isolated-environment launcher. The launcher requires Python 3.10–3.13 (64-bit) and downloads its dependencies on first use.

## Windows EXE and MSI

Use 64-bit Python, Node.js/npm, and WiX Toolset 3.11 on Windows. Run from the repository root:

```powershell
powershell -NoProfile -File scripts/build_windows_release.ps1 -Target All -WixDir C:/tools/wix311
```

The build creates an isolated `.build-venv`. To reuse a prepared environment, supply `-BuildEnvironment C:/path/to/build-venv -SkipInstall`. This re-installs the current tametools source without replacing other dependencies and runs `pip check`. `-SkipFrontend` is only appropriate after rebuilding `web/frontend/build` from the current source. The EXE and web launcher are collected into one folder. The MSI installs the complete folder, adds the CLI to PATH, and creates a Start menu shortcut.

## Assemble upload files

After building Python, frontend, and Windows artifacts:

```sh
python scripts/package_release.py
```

Files in `dist/0.4.0`:

- `tametools-0.4.0-py3-none-any.whl`: Python CLI/library
- `tametools-0.4.0.tar.gz`: Python source distribution
- `tametools-0.4.0-source.zip`: complete source checkout without Git history or build caches
- `tametools-0.4.0-browser-local.zip`: Chrome bundle, including the prebuilt frontend
- `tametools-0.4.0-windows-x64-portable.zip`: both EXEs and their complete runtime
- `tametools-0.4.0-x64.msi`: Windows installer
- `SOURCE_SHA256.json`: source-file checksums
- `SHA256SUMS.txt`: archive checksums
- `BUILD_VERIFICATION.json`: checks executed for this build, when present
- `windows-build-requirements.txt`: build environment package versions

## Upload to GitHub

Commit the program files from this checkout. `.gitignore` excludes `dist/`, build directories, local environments, and runtime outputs. Review `git status` and `git diff` before creating the commit.

Create a release for tag `v0.4.0` at the chosen source commit. Attach the files listed above from `dist/0.4.0` and use [the release notes](RELEASE_NOTES_v0.4.0.md) as its description. Upload the prebuilt browser ZIP as an explicit asset; the automatic GitHub source ZIP does not contain a frontend build.

Large installers belong in Release assets: [GitHub blocks ordinary Git files above 100 MiB](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github). See [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Publishing is a separate manual step; the packaging script creates local files only.
