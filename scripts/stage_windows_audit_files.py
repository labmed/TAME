"""Complete the onedir bundle with source hashes and version metadata for reports."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
from PyInstaller.utils.hooks import copy_metadata

root = Path(__file__).resolve().parents[1]
source = root / 'tametools/src/tametools'
app = Path(sys.argv[1]).resolve()
assert (app / 'tametools.exe').is_file() and (app / 'tametools-web.exe').is_file()
internal = app / '_internal'
assert internal.is_dir()
hashes = {}
for p in sorted(source.rglob('*.py')):
    rel = p.relative_to(source)
    dest = internal / 'tametools' / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dest)
    hashes[rel.as_posix()] = hashlib.sha256(dest.read_bytes()).hexdigest()
for distribution in ['tametools', 'numpy', 'pandas', 'scipy', 'python-docx', 'matplotlib',
                     'scikit-learn', 'statsmodels', 'samplics', 'pint', 'pyreadstat', 'pyarrow',
                     'fastapi', 'uvicorn']:
    for src, relative in copy_metadata(distribution):
        shutil.copytree(src, internal / relative, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('direct_url.json'))
        local_origin = internal / relative / 'direct_url.json'
        if local_origin.is_file():
            local_origin.unlink()
(app / 'IMPLEMENTATION_SHA256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
print(f'Staged {len(hashes)} source modules and report dependency metadata: {app}')
