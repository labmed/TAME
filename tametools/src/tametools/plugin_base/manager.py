from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

from ..config import ci_get
from ..models import OperationOutput, TameDataset
from .base import PLUGIN_REGISTRY, PluginSpec, normalize_plugin_name


# 플러그인은 한 종류뿐이다: 모두 plugins 폴더(파일 경로)에서 로드되는 드롭인이다.
# "신뢰(trusted)"는 배포에 함께 담겨 실행파일과 같이 배치되는 폴더 = 기본 로드(--allow-plugins 불필요):
#   - 패키지에 동봉된 plugins/ (개발/pip 설치)
#   - frozen onedir 빌드의 실행파일 옆 plugins\ (사용자가 편집/추가)
# "외부(external)"는 사용자가 임의로 지정하는 위치 = --allow-plugins 게이트:
#   - 환경변수 TAMETOOLS_PLUGIN_PATH, %LOCALAPPDATA%\tametools\plugins, META 선언 점 모듈
# 코어(cli/web/pipeline)는 플러그인을 "이름"으로만 호출하므로, plugins 폴더가 비거나 삭제돼도
# 프로그램 구동에는 문제가 없다(없는 이름은 "unknown plugin" 으로 처리된다).

_TRUSTED_LOADED = False
_TRUSTED_PLUGINS: dict[str, PluginSpec] = {}
_EXTERNAL_LOADED = False
_LOAD_ERRORS: list[str] = []


def list_plugins(meta: dict | None = None, *, allow_external: bool = False) -> list[PluginSpec]:
    _ensure_plugins_loaded(meta, allow_external=allow_external)
    registry = PLUGIN_REGISTRY if allow_external else _TRUSTED_PLUGINS
    return [registry[name] for name in sorted(registry)]


def get_plugin(name: str, meta: dict | None = None, *, allow_external: bool = False) -> PluginSpec | None:
    _ensure_plugins_loaded(meta, allow_external=allow_external)
    registry = PLUGIN_REGISTRY if allow_external else _TRUSTED_PLUGINS
    return registry.get(normalize_plugin_name(name))


def run_plugin(
    dataset: TameDataset,
    name: str,
    meta: dict | None,
    step_name: str,
    options: dict,
    *,
    allow_external: bool = False,
) -> OperationOutput | None:
    spec = get_plugin(name, meta, allow_external=allow_external)
    if spec is None:
        return None
    return spec.handler(dataset, meta or {}, step_name, options)


def configured_plugin_modules(meta: dict | None) -> list[str]:
    return _configured_plugin_modules(meta)


def _ensure_plugins_loaded(meta: dict | None = None, *, allow_external: bool = False) -> None:
    global _TRUSTED_LOADED, _TRUSTED_PLUGINS, _EXTERNAL_LOADED, _LOAD_ERRORS
    if not _TRUSTED_LOADED:
        _LOAD_ERRORS = []
        loaded_stems: set[str] = set()
        for directory in _trusted_plugin_directories():
            _load_directory(directory, loaded_stems, protect=False)
        _TRUSTED_PLUGINS = dict(PLUGIN_REGISTRY)
        _TRUSTED_LOADED = True

    if not allow_external:
        return

    # META(.tame)에 선언된 점 모듈(외부) — meta 가 호출마다 다를 수 있어 매번 시도(임포트는 idempotent).
    for module_name in _configured_plugin_modules(meta):
        try:
            importlib.import_module(module_name)
            _protect_trusted_names(module_name)
        except Exception as exc:  # 선언했지만 import 안 되는 모듈이 전체를 막지 않게 한다
            _LOAD_ERRORS.append(f"{module_name}: {type(exc).__name__}: {exc}")

    if not _EXTERNAL_LOADED:
        for directory in _external_plugin_directories():
            _load_directory(directory, set(), protect=True)
        _EXTERNAL_LOADED = True


def plugin_directories() -> list[Path]:
    """현재 스캔하는 모든 플러그인 폴더(신뢰 + 외부). 표시/디버깅용."""
    dirs = _trusted_plugin_directories() + _external_plugin_directories()
    seen: set[str] = set()
    result: list[Path] = []
    for directory in dirs:
        key = str(directory)
        if key not in seen:
            seen.add(key)
            result.append(directory)
    return result


def _trusted_plugin_directories() -> list[Path]:
    """기본 로드(신뢰) 폴더: frozen 실행파일 옆 plugins\\ → 패키지 동봉 plugins/.

    실행파일 옆 폴더를 먼저 둬서 사용자가 그 폴더에서 편집/교체한 파일이 우선하도록 한다.
    """
    dirs: list[Path] = []
    frozen_dir = _frozen_plugin_dir()
    if frozen_dir is not None:
        dirs.append(frozen_dir)
    dirs.append(_package_plugin_dir())
    seen: set[str] = set()
    result: list[Path] = []
    for directory in dirs:
        key = str(directory)
        if key not in seen:
            seen.add(key)
            result.append(directory)
    return result


def _external_plugin_directories() -> list[Path]:
    """--allow-plugins 로만 로드하는 사용자 지정 폴더: 환경변수 + 플랫폼 기본 사용자 폴더."""
    dirs: list[Path] = []
    env_value = os.environ.get("TAMETOOLS_PLUGIN_PATH", "")
    for part in env_value.split(os.pathsep):
        part = part.strip()
        if part:
            dirs.append(Path(part).expanduser())
    dirs.append(_default_plugin_dir())
    seen: set[str] = set()
    result: list[Path] = []
    for directory in dirs:
        key = str(directory)
        if key not in seen:
            seen.add(key)
            result.append(directory)
    return result


def _package_plugin_dir() -> Path:
    """패키지에 함께 배포되는 기본 플러그인 폴더(개발 src 레이아웃/pip 설치 모두 패키지 옆).

    frozen 빌드에서는 보통 존재하지 않으며(실행파일 옆 plugins\\ 를 대신 사용), 없으면 무시된다.
    """
    return Path(__file__).resolve().parent.parent / "plugins"


def _frozen_plugin_dir() -> Path | None:
    """PyInstaller onedir 빌드에서 실행파일과 같은 폴더의 ``plugins`` 디렉터리. 비-frozen 은 None."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent / "plugins"


def _default_plugin_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "tametools" / "plugins"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "tametools" / "plugins"


def plugin_load_errors() -> list[str]:
    """가장 최근 로딩에서 발생한 파일별 오류(깨진 플러그인은 건너뛰고 기록만 한다)."""
    return list(_LOAD_ERRORS)


def _load_directory(directory: Path, loaded_stems: set[str], *, protect: bool) -> None:
    if not directory.is_dir():
        return
    dir_str = str(directory)
    if dir_str not in sys.path:
        sys.path.insert(0, dir_str)  # 플러그인이 같은 폴더의 헬퍼 모듈을 import 할 수 있게
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        if not protect and path.stem in loaded_stems:
            continue  # 신뢰 폴더 간 같은 stem 중복 로드 방지(먼저 로드된 폴더가 우선)
        if _load_file(path):
            loaded_stems.add(path.stem)
        if protect:
            _protect_trusted_names(path.name)


def _load_file(path: Path) -> bool:
    module_name = f"tametools_dropin_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot create import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return True
    except Exception as exc:  # 한 파일이 깨져도 전체 로딩을 막지 않는다
        sys.modules.pop(module_name, None)
        _LOAD_ERRORS.append(f"{path.name}: {type(exc).__name__}: {exc}")
        return False


def _protect_trusted_names(filename: str) -> None:
    """외부 드롭인이 신뢰(동봉) 플러그인 이름을 덮어쓰지 못하게 복원한다(동봉본이 항상 우선)."""
    for name, trusted_spec in _TRUSTED_PLUGINS.items():
        if PLUGIN_REGISTRY.get(name) is not trusted_spec:
            PLUGIN_REGISTRY[name] = trusted_spec
            _LOAD_ERRORS.append(
                f"{filename}: plugin name '{name}' collides with a built-in plugin and was ignored."
            )


def _configured_plugin_modules(meta: dict | None) -> list[str]:
    if not isinstance(meta, dict):
        return []

    modules: list[str] = []
    plugins_section = ci_get(meta, "PLUGINS", {})
    if isinstance(plugins_section, dict):
        configured = ci_get(plugins_section, "MODULES", [])
        if isinstance(configured, list):
            modules.extend(str(item) for item in configured)

    use_section = ci_get(meta, "USE", {})
    if isinstance(use_section, dict):
        default_items = ci_get(use_section, "DEFAULT", [])
        if isinstance(default_items, list):
            modules.extend(str(item) for item in default_items if "." in str(item))

    seen: set[str] = set()
    result: list[str] = []
    for module_name in modules:
        if module_name not in seen:
            seen.add(module_name)
            result.append(module_name)
    return result
