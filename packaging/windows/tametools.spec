from pathlib import Path

import runpy
import tomli
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct


# onedir 통합 빌드: tametools.exe(CLI)와 tametools-web.exe(Web)를 하나의 폴더로 묶어
# _internal 의존성(pandas/matplotlib 등)을 공유한다. onefile 과 달리 실행 시 임시 폴더로
# 압축을 풀지 않으므로 첫 실행이 빠르고, 실행파일 옆 plugins\ 드롭인 폴더를 쓸 수 있다.
ROOT = Path(SPECPATH).parents[1]
SRC = ROOT / "tametools" / "src"
BACKEND = ROOT / "web" / "backend"
FRONTEND_BUILD = ROOT / "web" / "frontend" / "build"
CLI_LAUNCHER = ROOT / "packaging" / "windows" / "tametools_cli_launcher.py"
WEB_LAUNCHER = ROOT / "packaging" / "windows" / "tametools_web_launcher.py"
VERSION = tomli.loads((ROOT / "tametools" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
assert VERSION == runpy.run_path(str(SRC / "tametools" / "_version.py"))["__version__"]
VERSION_TUPLE = tuple(int(part) for part in VERSION.split(".")) + (0,)
version_resource = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE, mask=0x3f,
                     flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "tametools"),
        StringStruct("FileDescription", "tametools clinical data workbench"),
        StringStruct("FileVersion", VERSION),
        StringStruct("ProductName", "tametools"),
        StringStruct("ProductVersion", VERSION),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)


if not (FRONTEND_BUILD / "index.html").exists():
    raise SystemExit("web/frontend/build/index.html is missing. Run npm run build before PyInstaller.")


# Full Windows distribution: fail during build if a required feature is missing.
for package in ["scipy", "statsmodels.api", "sklearn", "samplics", "pint", "pyreadstat", "pyarrow", "matplotlib", "docx"]:
    __import__(package)
# The release script also stages exact source and dependency metadata for report
# audit hashes after COLLECT (scripts/stage_windows_audit_files.py).
report_datas = (collect_data_files("matplotlib") + collect_data_files("docx")
                + collect_data_files("pint") + copy_metadata("tametools"))
# 기본 플러그인은 plugins\ 폴더에서 "파일 경로"로 로드되므로 PyInstaller 정적분석이 그 import 를
# 보지 못한다. 플러그인이 쓰는 tametools 내부 모듈과 scipy 를 모두 hiddenimport 로 강제 포함한다.
tametools_hidden = collect_submodules("tametools") + collect_submodules("pyreadstat") + [
    "scipy.stats", "scipy.stats._stats_py", "statsmodels.api", "sklearn.metrics",
    "samplics", "pint", "pyreadstat", "pyarrow", "fastapi", "uvicorn",
]


a_cli = Analysis(
    [str(CLI_LAUNCHER)],
    pathex=[str(SRC)],
    binaries=[],
    datas=report_datas,
    hiddenimports=[*tametools_hidden, "matplotlib.backends.backend_agg"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

a_web = Analysis(
    [str(WEB_LAUNCHER)],
    pathex=[str(SRC), str(BACKEND)],
    binaries=[],
    datas=[(str(FRONTEND_BUILD), "web/frontend/build"), (str(BACKEND / "examples"), "examples"), *report_datas],
    hiddenimports=[
        "app.main",
        "app.tametools_bridge",
        "multipart",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
        *tametools_hidden,
        "matplotlib.backends.backend_agg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz_cli = PYZ(a_cli.pure)
pyz_web = PYZ(a_web.pure)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="tametools",
    version=version_resource,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_web = EXE(
    pyz_web,
    a_web.scripts,
    [],
    exclude_binaries=True,
    name="tametools-web",
    version=version_resource,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 두 EXE 를 하나의 COLLECT 로 묶어 _internal 의존성을 한 번만 담는다.
coll = COLLECT(
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    exe_web,
    a_web.binaries,
    a_web.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="tametools",
)
