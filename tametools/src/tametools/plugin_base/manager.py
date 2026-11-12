from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ci_get
from ..models import OperationOutput, TameDataset
from . import contract
from .base import PLUGIN_REGISTRY, PluginError, PluginSpec, normalize_plugin_name
from .extensions import COMMAND_REGISTRY, EXPORTER_REGISTRY, VIEW_REGISTRY, CommandSpec, ExporterSpec, ViewSpec, view_for
from .selection import selected_plugin_names, require_plugin_selected, inherit_plugin_selection, plugin_closure


# 플러그인은 .py 파일 하나, 또는 __init__.py 가 있는 폴더(패키지) 하나다. 로드되면서 @register_plugin 등으로 자신을 등록한다.
# "신뢰(trusted)" = 기본 로드(--allow-plugins 불필요):
#   - 패키지에 동봉된 plugins/ (개발/pip 설치)
#   - frozen onedir 빌드의 실행파일 옆 plugins\ (사용자가 편집/추가)
#   - 이 Python 환경에 pip 로 설치된 플러그인 패키지 (entry point 그룹 "tametools.plugins").
#     환경에 패키지를 설치하는 것 자체가 그 환경 관리자의 신뢰 행위이기 때문이다.
#     환경변수 TAMETOOLS_INSTALLED_PLUGINS=off 로 끌 수 있다.
# "외부(external)" = --allow-plugins 게이트(자료 파일과 함께 오거나 사용자가 쓸 수 있는 폴더에 있는 코드):
#   - 환경변수 TAMETOOLS_PLUGIN_PATH, %LOCALAPPDATA%\tametools\plugins, META 선언 점 모듈
# 먼저 로드된 이름이 이긴다: 동봉 > 설치 > 외부. 뒤에 온 같은 이름은 무시하고 기록한다.
# 코어(cli/web/pipeline)는 플러그인을 "이름"으로만 호출하므로, plugins 폴더가 비거나 삭제돼도
# 프로그램 구동에는 문제가 없다(없는 이름은 "unknown plugin" 으로 처리된다).

ENTRY_POINT_GROUP = "tametools.plugins"

_TRUSTED_LOADED = False
_TRUSTED_PLUGINS: dict[str, PluginSpec] = {}
_TRUSTED_COMMANDS: dict[str, CommandSpec] = {}
_TRUSTED_VIEWS: dict[str, ViewSpec] = {}
_TRUSTED_EXPORTERS: dict[str, ExporterSpec] = {}
_EXTERNAL_LOADED = False
_LOAD_ERRORS: list[str] = []
_NAME_CONFLICTS: dict[str, str] = {}     # plugin name -> the later source that also registered it and was ignored
_ORIGINS: dict[str, str] = {}            # plugin name -> "bundled" | "installed DIST VERSION" | "folder PATH" | "module NAME"
_ALIASES: dict[str, Any] = {}            # import name this loader gave to a plugin module -> that module (see _import_names)
_SELECTION_CONTEXT: ContextVar[dict | None] = ContextVar("tametools_plugin_selection", default=None)


@dataclass(frozen=True)
class _PluginRun:
    """What holds for the plugins running now. Whoever starts the outermost one decides it; the ones it calls read it."""
    allow_external: bool                           # may plugins from outside the trusted folders run
    chain: tuple[tuple[str, Any], ...] = ()        # (name, options) of the plugins running now, outermost first
    scope: Any = None                              # the provenance operation the innermost of them was noted in
    undeclared: Any = None                         # shared list of (plugin, the plugin it used without declaring it)


_RUN: ContextVar[_PluginRun | None] = ContextVar("tametools_plugin_run", default=None)
_CALLED: ContextVar[set | None] = ContextVar("tametools_plugins_called", default=None)
MAX_PLUGIN_DEPTH = 20      # plugins running inside plugins; deeper than this is a mistake, not a design


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
    allow_external: bool | None = None,
) -> OperationOutput | None:
    """Run a plugin by name. Returns None only when no plugin of that name is available.

    ``allow_external`` says whether plugins from outside the trusted folders may run. The host that starts a run
    decides that, once. A plugin that runs another plugin leaves it out: the inner run has what the person allowed
    for the outer one. Inside a running plugin ``True`` cannot widen that, and ``False`` narrows it.
    """
    from ..observation_contract import observation_info
    from ..measurement_tags import require_measurement_tags
    observation_info(dataset)
    require_measurement_tags(dataset)
    outer = _RUN.get()
    allowed = bool(allow_external) if outer is None else (outer.allow_external and (allow_external is None or bool(allow_external)))
    # A composed plugin cannot lose its file's choice when an intermediate output
    # has fresh META. ContextVar also keeps concurrent files' choices independent.
    policy = _SELECTION_CONTEXT.get()
    if policy is None:
        policy = dataset.meta if selected_plugin_names(dataset.meta) is not None else (meta or {})
    selected = selected_plugin_names(policy)
    spec = get_plugin(name, meta, allow_external=allowed)
    registry = PLUGIN_REGISTRY if allowed else _TRUSTED_PLUGINS
    require_plugin_selected(name, policy, registry.values())
    if spec is None:
        return None
    _require_versions(spec, registry)
    chain = outer.chain if outer is not None else ()
    _require_an_end(spec, options, chain)
    from ..provenance import record_effective_parameters, record_plugin_call
    # The caller's record stays the caller's: a plugin run inside another one is noted beside it (PLUGIN_CALLS).
    scope = record_plugin_call(_implementation(spec), options, within=outer.scope if outer is not None else None)
    if selected is not None:
        record_effective_parameters({'PLUGIN_SELECTION': selected})
    called = _CALLED.get()
    if called is not None:
        called.add(spec.name)
    undeclared = outer.undeclared if outer is not None else []
    _note_undeclared(outer, spec.name, registry)
    token = _SELECTION_CONTEXT.set({"PLUGINS": deepcopy(ci_get(policy, "PLUGINS", {}))})
    run_token = _RUN.set(_PluginRun(allowed, (*chain, (spec.name, options)), scope, undeclared))
    try:
        from ..dialogs import closed
        # A result must follow from the data and the options alone, so nothing is asked while a plugin computes.
        with closed(f"{spec.name}: 플러그인이 계산하는 동안에는 물을 수 없습니다. 질문은 register_plugin(form=...) 으로 선언하세요"):
            output = spec.handler(dataset, meta or {}, step_name, options)
        if not isinstance(output, OperationOutput):
            # Returning None used to read as "unknown plugin", and any other object failed somewhere inside the core.
            returned = ("returned nothing (is a `return` missing?)" if output is None
                        else f"returned {type(output).__name__}")
            raise PluginError(f"Plugin {spec.name} must return an OperationOutput, but its handler {returned}. "
                              "To hand back a table: return OperationOutput(name=step_name, table=frame)")
        contract.check_declared_output(spec, output)
        if outer is None and undeclared:
            # Said to whoever runs the plugin, so that its author hears of it before a file with ENABLED refuses the run.
            output.warnings = [*(output.warnings or []), *(
                f"{user} used {used} without declaring it. Add it to register_plugin(dependencies=(\"{used}\",)): "
                "a file that selects its plugins (META[PLUGINS].ENABLED) refuses a plugin that is not declared."
                for user, used in dict.fromkeys(undeclared))]
        if output.dataset is not None:
            # The result says which plugin returned it (not the name of the step it ran as), so its screen is found.
            output.dataset = _claimed(output.dataset, spec, allowed, output.status)
        if output.dataset is not None and selected is not None:
            target = output.dataset
            updated_meta = inherit_plugin_selection(target.meta, policy)
            if updated_meta != target.meta:
                from ..provenance import append_log_entry
                output.dataset = append_log_entry(target.replace(meta=updated_meta),
                    action='PLUGIN_SELECTION_INHERIT', input_dataset=target,
                    parameters={'ENABLED': selected},
                    message='입력 TAME의 플러그인 선택을 결과에 유지했습니다.', status=output.status)
        return output
    finally:
        _RUN.reset(run_token)
        _SELECTION_CONTEXT.reset(token)


def _note_undeclared(outer: _PluginRun | None, used: str, registry: dict[str, PluginSpec]) -> None:
    """Remember that the running plugin used ``used`` although its ``dependencies`` (followed through) do not name it."""
    if outer is None or not outer.chain or outer.undeclared is None:
        return
    user = registry.get(outer.chain[-1][0])
    if user is None or user.name == used:
        return
    declared, _, _ = plugin_closure([user.name], registry)
    if used not in declared:
        outer.undeclared.append((user.name, used))


def _claim(dataset: TameDataset, spec: PluginSpec, allowed: bool) -> TameDataset:
    registry = PLUGIN_REGISTRY if allowed else _TRUSTED_PLUGINS
    views = VIEW_REGISTRY if allowed else _TRUSTED_VIEWS
    return contract.claim_result(dataset, spec, same=[key for key, item in registry.items() if item.handler is spec.handler],
                                 is_plugin=lambda other: other in registry, has_view=lambda other: other in views)


def _claimed(dataset: TameDataset, spec: PluginSpec, allowed: bool, status: str) -> TameDataset:
    """The dataset a plugin returned, saying that this plugin returned it.

    A dataset whose history already describes it exactly as it is (a workflow step's result handed back as it was)
    is not changed behind that record: the change is written into the history as well, so the record still verifies.
    """
    claimed = _claim(dataset, spec, allowed)
    if claimed is dataset:
        return dataset
    from ..provenance import append_log_entry, log_entries
    from ..provenance_audit import artifact_descriptor
    recorded = next((artifact for entry in reversed(log_entries(dataset)) for artifact in reversed(entry.get('OUTPUTS', []))
                     if artifact.get('KIND') == 'dataset'), None)
    if recorded is None:
        return claimed
    observed = artifact_descriptor(dataset)
    if any(recorded.get(key) != observed[key] for key in ('DATA_SHA256', 'CONTROL_SHA256')):
        return claimed          # changed since its last record anyway; whoever runs the plugin records the outcome
    return append_log_entry(claimed, action='PLUGIN_RESULT', input_dataset=dataset, status=status,
                            parameters={'PLUGIN': spec.name, 'ANALYSIS_RESULT': deepcopy(ci_get(claimed.meta, contract.RESULT_SECTION, {}))},
                            message=f'{spec.name} 이 돌려준 결과임을 표시했습니다.')


def claim_running(dataset: TameDataset) -> TameDataset:
    """Mark ``dataset`` as the result of the plugin that is running now.

    For code that records a result before the plugin returns it (``PluginWorkflow.finish``): the mark is in place
    when the record is written, and nothing has to be changed afterwards.
    """
    run = _RUN.get()
    if run is None or not run.chain:
        return dataset
    spec = (PLUGIN_REGISTRY if run.allow_external else _TRUSTED_PLUGINS).get(run.chain[-1][0])
    return dataset if spec is None else _claim(dataset, spec, run.allow_external)


def _implementation(spec: PluginSpec) -> dict:
    """What is written into the log about the code that ran."""
    implementation = {'NAME': spec.name, 'MODULE': spec.handler.__module__}
    if spec.version:
        implementation['VERSION'] = spec.version
    try:
        source = inspect.getsourcefile(inspect.unwrap(spec.handler))
    except (TypeError, OSError):
        source = None
    if source and Path(source).is_file():
        implementation['SOURCE_SHA256'] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if _ORIGINS.get(spec.name, "").startswith("installed "):
        implementation['DISTRIBUTION'] = _ORIGINS[spec.name][len("installed "):]
    return implementation


def _require_versions(spec: PluginSpec, registry: dict[str, PluginSpec]) -> None:
    unmet = contract.unmet_requirements(spec, registry)
    if unmet:
        detail = "; ".join(f"{name} {wanted} or later, but {name} {found or 'without a declared version'} is here"
                           + (f" ({_ORIGINS[name]})" if _ORIGINS.get(name) else "") for name, wanted, found in unmet)
        raise PluginError(f"Plugin {spec.name} needs {detail}. Update that plugin, or use the {spec.name} release made for it")


def _require_an_end(spec: PluginSpec, options: Any, chain: tuple[tuple[str, Any], ...]) -> None:
    """Two plugins that run each other would go on until Python gives up; say so at the second turn."""
    def same(earlier: Any) -> bool:
        try:
            return bool(earlier == options)
        except Exception:
            return False

    repeated = any(name == spec.name and same(earlier) for name, earlier in chain)
    if repeated or len(chain) >= MAX_PLUGIN_DEPTH:
        names = [name for name, _ in chain]
        shown = ("... -> " if len(names) > 6 else "") + " -> ".join([*names[-6:], spec.name])
        reason = "Plugins run each other without end" if repeated else f"Plugins are nested more than {MAX_PLUGIN_DEPTH} deep"
        raise PluginError(f"{reason}: {shown}. A plugin may run another plugin, but not one that is already running with the same options")


@contextmanager
def called_plugins():
    """Collect the names of the plugins that actually run inside the block, however they were reached.

    A plugin may run another one without declaring it; what was really run is what a sync has to watch.
    """
    names: set[str] = set()
    outer = _CALLED.get()
    token = _CALLED.set(names)
    try:
        yield names
    finally:
        _CALLED.reset(token)
        if outer is not None:
            outer.update(names)


def plugin_module(name: str, *, allow_external: bool | None = None):
    """The Python module that registered the plugin ``name``, to use its functions the way an import would.

        stats = plugin_module("A_STATS")
        table = stats.summarize(dataset.df)

    Unlike ``import a_stats`` this does not depend on how or where the plugin is installed (a file in a folder, a
    package, the plugins that come with tametools, a frozen build), never loads it a second time, follows the
    same rules as running it (what the person allowed, ``META[PLUGINS].ENABLED``, ``dependencies``), and is noted
    in the log beside the calling plugin. Call it inside the handler: while plugin files are still being read,
    the other plugin may not be there yet.
    """
    outer = _RUN.get()
    allowed = bool(allow_external) if outer is None else (outer.allow_external and (allow_external is None or bool(allow_external)))
    _ensure_plugins_loaded(None, allow_external=allowed)
    registry = PLUGIN_REGISTRY if allowed else _TRUSTED_PLUGINS
    wanted = normalize_plugin_name(name)
    policy = _SELECTION_CONTEXT.get()
    if policy is not None:
        require_plugin_selected(wanted, policy, registry.values())
    spec = registry.get(wanted)
    if spec is None:
        hints = unknown_plugin_hints(wanted, None, allow_external=allowed)
        raise PluginError(f"plugin_module: no plugin named {wanted} is available here" + ("".join(" (" + hint + ")" for hint in hints[:2])))
    module = inspect.getmodule(inspect.unwrap(spec.handler))
    if module is None:
        raise PluginError(f"plugin_module: the module of {wanted} cannot be found")
    from ..provenance import record_plugin_call
    if outer is not None:
        record_plugin_call({**_implementation(spec), 'USE': 'module'}, {}, within=outer.scope)
        _note_undeclared(outer, spec.name, registry)
    called = _CALLED.get()
    if called is not None:
        called.add(spec.name)
    return module


def require_result(dataset: TameDataset, plugin: str, *, columns=()) -> dict[str, str]:
    """Check that ``dataset`` is a result of ``plugin`` and has the columns about to be read; see ``contract.require_result``."""
    outer = _RUN.get()
    registry = PLUGIN_REGISTRY if (outer is not None and outer.allow_external) else _TRUSTED_PLUGINS
    spec = registry.get(normalize_plugin_name(plugin))
    same = [key for key, item in registry.items() if spec is not None and item.handler is spec.handler]
    return contract.require_result(dataset, plugin, columns=columns, same=same)


def configured_plugin_modules(meta: dict | None) -> list[str]:
    return _configured_plugin_modules(meta)


def _ensure_plugins_loaded(meta: dict | None = None, *, allow_external: bool = False) -> None:
    global _TRUSTED_LOADED, _TRUSTED_PLUGINS, _TRUSTED_COMMANDS, _TRUSTED_VIEWS, _TRUSTED_EXPORTERS, _EXTERNAL_LOADED, _LOAD_ERRORS
    if not _TRUSTED_LOADED:
        _LOAD_ERRORS = []
        _NAME_CONFLICTS.clear()
        _ORIGINS.clear()
        loaded_stems: set[str] = set()
        for directory in _trusted_plugin_directories():
            _load_directory(directory, loaded_stems, protected=None)
        _load_entry_points(dict(PLUGIN_REGISTRY))
        _TRUSTED_PLUGINS = dict(PLUGIN_REGISTRY)
        _TRUSTED_COMMANDS = dict(COMMAND_REGISTRY)
        _TRUSTED_VIEWS = dict(VIEW_REGISTRY)
        _TRUSTED_EXPORTERS = dict(EXPORTER_REGISTRY)
        _TRUSTED_LOADED = True

    if not allow_external:
        return

    # META(.tame)에 선언된 점 모듈(외부) — meta 가 호출마다 다를 수 있어 매번 시도(임포트는 idempotent).
    for module_name in _configured_plugin_modules(meta):
        before = _snapshot()
        try:
            importlib.import_module(module_name)
            _settle(before, _TRUSTED_PLUGINS, module_name, f"module {module_name}")
        except Exception as exc:  # 선언했지만 import 안 되는 모듈이 전체를 막지 않게 한다
            _note_error(f"{module_name}: {type(exc).__name__}: {exc}")

    if not _EXTERNAL_LOADED:
        for directory in _external_plugin_directories():
            _load_directory(directory, set(), protected=_TRUSTED_PLUGINS)
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


def _note_error(text: str) -> None:
    if text not in _LOAD_ERRORS:      # a META module is tried at every call; say it once
        _LOAD_ERRORS.append(text)


def _plugin_units(directory: Path) -> list[Path]:
    """What one plugin folder holds: ``name.py`` files and ``name/`` packages (folders with ``__init__.py``).

    Names starting with ``_`` are helpers, not plugins. Other subfolders (``examples``) are left alone.
    """
    units = [path for path in directory.glob("*.py") if not path.name.startswith("_")]
    units += [path for path in directory.iterdir()
              if path.is_dir() and not path.name.startswith(("_", ".")) and (path / "__init__.py").is_file()]
    return sorted(units, key=lambda path: path.name)


def _load_directory(directory: Path, loaded_stems: set[str], *, protected: dict | None) -> None:
    """Load every plugin of a folder. ``protected`` (None for the bundled folders) holds the names that may not be replaced."""
    if not directory.is_dir():
        return
    dir_str = str(directory)
    if dir_str not in sys.path:
        sys.path.insert(0, dir_str)  # 플러그인이 같은 폴더의 헬퍼 모듈을 import 할 수 있게
    present = set(sys.modules)       # what is imported from here on was imported by the plugins of this folder
    for path in _plugin_units(directory):
        stem = path.stem if path.is_file() else path.name
        if protected is None and stem in loaded_stems:
            continue  # 신뢰 폴더 간 같은 stem 중복 로드 방지(먼저 로드된 폴더가 우선)
        before = _snapshot()
        if _load_unit(path, present):
            loaded_stems.add(stem)
        _settle(before, protected, path.name, "bundled" if protected is None else f"folder {path}", directory)


def _same_file(first: Any, second: Any) -> bool:
    if not first or not second:
        return False
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))
    except OSError:
        return False


def _import_names(stem: str, target: Path) -> list[str]:
    """The names under which an ``import`` statement would load this very plugin file.

    A plugin is loaded by its path under a name of its own (``tametools_dropin_<stem>``). Its folder is on
    ``sys.path`` too, so ``import <stem>`` in a sibling plugin would find the same file and run it a second time:
    two copies of its functions and state, and its plugins registered twice. Giving the loaded module these names
    as well makes the import return what is already loaded.
    """
    names: list[str] = []
    if stem.isidentifier() and stem not in getattr(sys, "stdlib_module_names", ()):
        try:
            found = importlib.machinery.PathFinder.find_spec(stem)      # looks the file up; imports nothing
        except Exception:
            found = None
        if found is not None and _same_file(found.origin, target):
            names.append(stem)
    unit = target.parent if target.name == "__init__.py" else target
    if _same_file(unit.parent, _package_plugin_dir()):
        names.append(f"{__name__.split('.')[0]}.plugins.{stem}")         # the plugins that come with tametools
    return names


def _load_unit(path: Path, present: set[str] | frozenset[str] = frozenset()) -> bool:
    """Load one plugin file or package by its path (no PYTHONPATH and no dotted name needed, so a frozen build can do it).

    ``present`` holds the modules that were loaded before this folder was read. A module that is not in it but is
    this very file was imported a moment ago by a sibling plugin (``import a_stats``): it is used as it is.
    """
    package = path.is_dir()
    stem = path.name if package else path.stem
    module_name = f"tametools_dropin_{stem}"
    target = path / "__init__.py" if package else path
    aliases = _import_names(stem, target)
    for alias in aliases:
        existing = sys.modules.get(alias)
        if existing is not None and alias not in present and _same_file(getattr(existing, "__file__", None), target):
            sys.modules[module_name] = existing
            return True
    claimed: list[tuple[str, Any]] = []
    module = None
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, target, submodule_search_locations=[str(path)] if package else None)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot create import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        for alias in aliases:
            existing = sys.modules.get(alias)
            # A name is taken only when it is free, was given by this loader before, or already means this file.
            if existing is None or existing is _ALIASES.get(alias) or _same_file(getattr(existing, "__file__", None), target):
                claimed.append((alias, existing))
                sys.modules[alias] = module
        spec.loader.exec_module(module)
        for alias, _ in claimed:
            _ALIASES[alias] = module
            parent_name, _, child = alias.rpartition(".")
            parent = sys.modules.get(parent_name) if parent_name else None
            if parent is not None:
                setattr(parent, child, module)
        return True
    except Exception as exc:  # 한 파일이 깨져도 전체 로딩을 막지 않는다
        for loaded in [name for name in sys.modules if name == module_name or name.startswith(module_name + ".")]:
            sys.modules.pop(loaded, None)
        for alias, existing in claimed:
            if sys.modules.get(alias) is module:
                if existing is None:
                    sys.modules.pop(alias, None)
                else:
                    sys.modules[alias] = existing
        _note_error(f"{path.name}: {type(exc).__name__}: {exc}")
        return False


def _load_entry_points(bundled: dict[str, PluginSpec]) -> None:
    """Plugins installed with pip: every distribution that declares an entry point in the group ``tametools.plugins``.

    The entry point names a module; importing it registers its plugins.
    """
    if os.environ.get("TAMETOOLS_INSTALLED_PLUGINS", "").strip().lower() in {"off", "0", "no", "false"}:
        return
    try:
        from importlib.metadata import entry_points
        points = sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda point: (point.name, point.value))
    except Exception as exc:
        _note_error(f"installed plugins: {type(exc).__name__}: {exc}")
        return
    for point in points:
        distribution = getattr(point, "dist", None)
        label = f"{distribution.name} {distribution.version}" if distribution is not None else point.value
        before = _snapshot()
        try:
            importlib.import_module(point.value.split(":", 1)[0].strip())
        except Exception as exc:
            _note_error(f"{label} ({point.value}): {type(exc).__name__}: {exc}")
            continue
        _settle(before, bundled, label, f"installed {label}")


def _snapshot() -> tuple[dict, dict, dict, dict]:
    return dict(PLUGIN_REGISTRY), dict(COMMAND_REGISTRY), dict(VIEW_REGISTRY), dict(EXPORTER_REGISTRY)


def _code_file(function: Any) -> str:
    """The file a registered function was written in ('' when that cannot be told)."""
    try:
        source = inspect.getsourcefile(inspect.unwrap(function))
    except (TypeError, OSError):
        return ""
    return os.path.normcase(str(Path(source).resolve())) if source else ""


def _same_code(first: Any, second: Any) -> bool:
    """Two registrations of one name that come from the same file: that file was imported twice, nobody took a name."""
    return first is not None and second is not None and bool(_code_file(first)) and _code_file(first) == _code_file(second)


def _origin_of(spec: PluginSpec, origin: str, directory: Path | None, bundled: bool) -> str:
    """Where a plugin registered during a load really is.

    A plugin file that imports a sibling plugin file registers that file's plugins while it loads; they are
    the sibling's, not its own.
    """
    if directory is None:
        return origin
    source = _code_file(spec.handler)
    if not source:
        return origin
    try:
        relative = Path(source).relative_to(Path(os.path.normcase(str(directory.resolve()))))
    except ValueError:
        return origin
    if not relative.parts:
        return origin
    unit = next((path for path in _plugin_units(directory) if os.path.normcase(path.name) == relative.parts[0]), None)
    return origin if unit is None else ("bundled" if bundled else f"folder {unit}")


def _settle(before: tuple[dict, dict, dict, dict], protected: dict[str, PluginSpec] | None, source: str, origin: str,
            directory: Path | None = None) -> None:
    """After one file, package or module was loaded: undo what it may not replace, and note where its plugins came from.

    Only the names this load registered are attributed to it. A bundled plugin module that some other code imported
    a second time also changes the registries; that is put back quietly and is nobody's name collision.
    """
    registered = {name: spec for name, spec in PLUGIN_REGISTRY.items() if before[0].get(name) is not spec}
    if protected is not None:
        _protect(protected, source, set(registered))
    if protected is _TRUSTED_PLUGINS:       # the same holds for what trusted plugins added to the CLI and the screens
        for registry, earlier, trusted, what, function in (
                (COMMAND_REGISTRY, before[1], _TRUSTED_COMMANDS, "command", "run"),
                (VIEW_REGISTRY, before[2], _TRUSTED_VIEWS, "view", "render"),
                (EXPORTER_REGISTRY, before[3], _TRUSTED_EXPORTERS, "export format", "write")):
            added = {name for name, spec in registry.items() if earlier.get(name) is not spec}
            for name, spec in trusted.items():
                current = registry.get(name)
                if current is not spec:
                    registry[name] = spec
                    if name in added and not _same_code(getattr(current, function, None), getattr(spec, function, None)):
                        _note_error(f"{source}: {what} '{name}' is already provided by a trusted plugin and was ignored.")
    for name, spec in registered.items():
        if PLUGIN_REGISTRY.get(name) is spec:
            _ORIGINS[name] = _origin_of(spec, origin, directory, bundled=protected is None)


def _protect(protected: dict[str, PluginSpec], source: str, registered: set[str] | None = None) -> None:
    """먼저 로드된 플러그인 이름을 뒤에 온 것이 덮어쓰지 못하게 복원한다(동봉본이 항상 우선).

    ``registered`` 는 방금 읽은 파일이 등록한 이름이다. 그 이름만 충돌로 기록한다(None 이면 바뀐 이름 전부).
    같은 파일이 한 번 더 import 되어 생긴 재등록은 충돌이 아니므로 조용히 되돌린다.
    """
    for name, spec in protected.items():
        current = PLUGIN_REGISTRY.get(name)
        if current is not spec:
            PLUGIN_REGISTRY[name] = spec
            if (registered is None or name in registered) and not _same_code(getattr(current, "handler", None), spec.handler):
                _NAME_CONFLICTS[name] = source
                _note_error(f"{source}: plugin name '{name}' collides with a built-in plugin and was ignored.")


def _protect_trusted_names(filename: str) -> None:
    _protect(_TRUSTED_PLUGINS, filename)


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


# --------------------------------------------------------------------------------------------------------------------
# For the people who write and run plugins: where a plugin came from, and why one is not there
# --------------------------------------------------------------------------------------------------------------------

def plugin_origin(name: str) -> str:
    """Where a loaded plugin came from: ``bundled``, ``installed DIST VERSION``, ``folder PATH`` or ``module NAME``."""
    return _ORIGINS.get(normalize_plugin_name(name), "")


def plugin_name_conflicts() -> dict[str, str]:
    """Plugin names that a later file also registered and lost: name -> that file."""
    return dict(_NAME_CONFLICTS)


def _declared_names(path: Path) -> set[str]:
    """Plugin names a file registers, read from its text (the file is not run)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {normalize_plugin_name(name) for name in re.findall(r"register_plugin\(\s*(?:name\s*=\s*)?[\"']([^\"']+)[\"']", text)}


def unknown_plugin_hints(name: str, meta: dict | None = None, *, allow_external: bool = False) -> list[str]:
    """Why a plugin name is not available, as far as that can be told without running untrusted code."""
    wanted = normalize_plugin_name(name)
    hints = [f"a plugin file was not loaded: {error}" for error in _LOAD_ERRORS]
    if allow_external:
        return hints
    files = [path for directory in _external_plugin_directories() if directory.is_dir()
             for unit in _plugin_units(directory) for path in ([unit] if unit.is_file() else sorted(unit.rglob("*.py")))]
    defined = [path for path in files if wanted in _declared_names(path)]
    modules = _configured_plugin_modules(meta)
    if defined:
        hints.append(f"{wanted} is defined in {defined[0]}, which is outside the trusted plugin folders. "
                     "Re-run with --allow-plugins if you trust that file.")
    elif files or modules:
        where = [str(directory) for directory in _external_plugin_directories() if directory.is_dir() and _plugin_units(directory)]
        hints.append("plugins outside the trusted folders were not loaded (" + ", ".join([*where, *modules])
                     + "). Re-run with --allow-plugins if you trust them.")
    return hints


def same_plugin_names(name: str, meta: dict | None = None, *, allow_external: bool = False) -> list[str]:
    """Every name under which the same plugin is registered (a plugin and its aliases share one handler)."""
    spec = get_plugin(name, meta, allow_external=allow_external)
    if spec is None:
        return []
    registry = PLUGIN_REGISTRY if allow_external else _TRUSTED_PLUGINS
    return sorted(key for key, item in registry.items() if item.handler is spec.handler)


def panel_plugin(panel: str, meta: dict | None = None, *, allow_external: bool = False) -> PluginSpec | None:
    """The plugin that declared it is run through the host screen ``panel`` (``register_plugin(panel=...)``).

    The declaring name is the one the host uses (settings section, log); the plugin's other names lead to it.
    Trusted plugins come first, so a plugin from outside cannot take a screen away from a bundled one.
    """
    wanted = str(panel or "").strip().lower()
    if not wanted:
        return None
    _ensure_plugins_loaded(meta, allow_external=allow_external)
    for registry in ((_TRUSTED_PLUGINS, PLUGIN_REGISTRY) if allow_external else (_TRUSTED_PLUGINS,)):
        for name in sorted(registry):
            if registry[name].panel == wanted:
                return registry[name]
    return None


def plugin_commands(*, allow_external: bool = False) -> dict[str, CommandSpec]:
    """Commands that plugins added to the command line (``register_command``)."""
    _ensure_plugins_loaded(None, allow_external=allow_external)
    return dict(COMMAND_REGISTRY if allow_external else _TRUSTED_COMMANDS)


def plugin_exporters(*, allow_external: bool = False) -> dict[str, ExporterSpec]:
    """Export formats that plugins added (``register_exporter``)."""
    _ensure_plugins_loaded(None, allow_external=allow_external)
    return dict(EXPORTER_REGISTRY if allow_external else _TRUSTED_EXPORTERS)


def plugin_view(dataset: TameDataset, *, allow_external: bool = False) -> ViewSpec | None:
    """The screen a plugin registered for this result (``register_view``), if any."""
    _ensure_plugins_loaded(getattr(dataset, "meta", None), allow_external=allow_external)
    spec = view_for(dataset)
    if spec is None or allow_external:
        return spec
    return spec if _TRUSTED_VIEWS.get(spec.name) is spec else None


def _source_files(spec: PluginSpec) -> list[Path]:
    """The files a plugin's behaviour depends on: its package, or its file and the ``_helper.py`` files beside it."""
    try:
        source = inspect.getsourcefile(inspect.unwrap(spec.handler))
    except (TypeError, OSError):
        return []
    if not source or not Path(source).is_file():
        return []
    path = Path(source).resolve()
    module = sys.modules.get(spec.handler.__module__)
    package = getattr(module, "__package__", "") or ""
    if package and not _ORIGINS.get(spec.name, "").startswith("module "):
        package = package.split(".")[0]      # a plugin package is taken whole; a META module only with its own package
    roots = [Path(item) for item in getattr(sys.modules.get(package), "__path__", [])] if package else []
    if roots:
        return sorted({item.resolve() for root in roots for item in root.rglob("*.py")})
    return sorted({path, *(item.resolve() for item in path.parent.glob("_*.py")), *_local_imports(module, path.parent)})


def _local_imports(module: Any, folder: Path) -> set[Path]:
    """Files of the plugin's own folder that it uses like modules (``from a_stats import summarize``), followed through.

    Such a file is part of what the plugin computes, although it is neither a ``_helper.py`` nor a declared
    dependency. Files of the tametools package are left out: the tool's own hash covers them.
    """
    package_root = Path(__file__).resolve().parents[1]
    found: set[Path] = set()
    queue, seen = [module], set()
    while queue:
        current = queue.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        for value in list(vars(current).values()):
            try:
                other = value if inspect.ismodule(value) else sys.modules.get(getattr(value, "__module__", None) or "")
                file = getattr(other, "__file__", None)
            except Exception:
                continue
            if other is None or other is current or not isinstance(file, str) or not file.endswith(".py"):
                continue
            path = Path(file).resolve()
            if folder in path.parents and package_root not in path.parents and path != package_root and path not in found:
                found.add(path)
                queue.append(other)
    return found


def declared_plugin_names() -> set[str]:
    """Names of the plugins that are loaded, and of those the plugin folders declare (read from the files' text; nothing is run)."""
    _ensure_plugins_loaded(None, allow_external=False)
    names = set(PLUGIN_REGISTRY)
    for directory in _external_plugin_directories():
        if directory.is_dir():
            for unit in _plugin_units(directory):
                for path in ([unit] if unit.is_file() else sorted(unit.rglob("*.py"))):
                    names |= _declared_names(path)
    return names


def plugin_sources(names, meta: dict | None = None, *, allow_external: bool = False) -> dict[str, str]:
    """SHA-256 of the code of the named plugins, and of the plugins they depend on, that live outside the tametools package.

    Code inside the package is already covered by the tool's own implementation hash. This is what lets a
    sync notice that a plugin dropped into a folder or installed with pip has changed.
    """
    _ensure_plugins_loaded(meta, allow_external=allow_external)
    registry = PLUGIN_REGISTRY if allow_external else _TRUSTED_PLUGINS
    known = [normalize_plugin_name(name) for name in names if normalize_plugin_name(name) in registry]
    effective, _, _ = plugin_closure(known, registry)
    package_root = Path(__file__).resolve().parents[1]
    result: dict[str, str] = {}
    for name in sorted(effective):
        spec = registry.get(name)
        files = [path for path in (_source_files(spec) if spec else []) if package_root not in path.parents]
        if not files:
            continue
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        result[name] = digest.hexdigest()
    return result
