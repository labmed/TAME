"""Assemble local release assets from the current application checkout."""
from pathlib import Path
import hashlib
import json
import os
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.4.0"
DIST = ROOT / "dist" / VERSION
SKIP = {".git", "node_modules", ".svelte-kit", "__pycache__", ".pytest_cache",
        ".build-venv", ".venv", ".browser-runtime", ".web-runtime", "dist", "build",
        "outputs", "tametools_failed_runs"}
TOP = {"tametools", "web", "docs", "tutorial", "packaging", "scripts", "tests"}
ROOT_FILES = {"README.md", "README_BROWSER_KO.md", "TAME_FORMAT_SPECIFICATION.md",
              "Start_Chrome.cmd", "Start_Chrome.command", "Start_Chrome.sh",
              "pytest.ini", ".gitignore", ".gitattributes"}

def source_files():
    for directory, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP
                         and not d.endswith((".egg-info", "_charts"))
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            p = Path(directory) / name
            rel = p.relative_to(ROOT)
            if not p.is_file() or p.is_symlink(): continue
            if p.suffix.lower() in {".pyc", ".pyo", ".log", ".wbk", ".docx", ".pdf"}: continue
            if p.name.startswith((".env", "~$")): continue
            if len(rel.parts) == 1 and p.name in ROOT_FILES: yield p
            elif rel.parts[0] in TOP: yield p

def archive(path, files, prefix, base=ROOT):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, (Path(prefix) / p.relative_to(base)).as_posix())

def main():
    DIST.mkdir(parents=True, exist_ok=True)
    files = list(source_files())
    assert files and (ROOT / "web/frontend/build/index.html").is_file()
    assert (DIST / f"tametools-{VERSION}-py3-none-any.whl").is_file()
    archive(DIST / f"tametools-{VERSION}-source.zip", files, "TAME")
    browser = [p for p in files if p.relative_to(ROOT).parts[0] not in {"tests"}
               and "tests" not in p.relative_to(ROOT).parts]
    browser += sorted(p for p in (ROOT / "web/frontend/build").rglob("*") if p.is_file())
    archive(DIST / f"tametools-{VERSION}-browser-local.zip", browser, f"tametools-{VERSION}-browser-local")
    app = DIST / "windows-exe/tametools"
    if app.is_dir():
        archive(DIST / f"tametools-{VERSION}-windows-x64-portable.zip",
                sorted(p for p in app.rglob("*") if p.is_file()), "tametools", app)
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (DIST / "SOURCE_SHA256.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    assets = sorted(p for p in DIST.iterdir() if p.is_file() and p.suffix in {".whl", ".gz", ".zip", ".msi", ".json", ".txt"}
                    and p.name != "SHA256SUMS.txt")
    (DIST / "SHA256SUMS.txt").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n" for p in assets), encoding="utf-8")
    print(json.dumps({"source_files":len(files), "assets":[{"name":p.name,"bytes":p.stat().st_size} for p in assets]}, indent=2))

if __name__ == "__main__": main()
