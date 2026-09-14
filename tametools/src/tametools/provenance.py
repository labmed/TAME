"""Versioned execution records shared by the CLI, pipelines and web workbench."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import time
from typing import Any
import uuid

from .config import ci_get
from .models import TameDataset
from .toml_compat import dumps as dumps_toml

SCHEMA_VERSION = 2
HASH_SPEC = 'tame-provenance-json-v2/sha256'
STATUSES = {'SUCCEEDED', 'FAILED', 'SKIPPED', 'CANCELLED'}


def _toml_safe(value: Any) -> Any:
    """Losslessly distinguish optional/nonfinite log values from empty strings."""
    if value is None:
        return {'__TAME_LOG_TYPE__': 'null'}
    if isinstance(value, dict):
        return {str(k): _toml_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_toml_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {'__TAME_LOG_TYPE__': 'float', 'VALUE': str(value)}
    if isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, 'item'):
        return _toml_safe(value.item())
    return str(value)


def restore_log_values(value):
    if isinstance(value, dict):
        if value == {'__TAME_LOG_TYPE__': 'null'}:
            return None
        if set(value) == {'__TAME_LOG_TYPE__', 'VALUE'} and value['__TAME_LOG_TYPE__'] == 'float':
            return float(value['VALUE'])
        return {k: restore_log_values(v) for k, v in value.items()}
    return [restore_log_values(v) for v in value] if isinstance(value, list) else value


def canonical_json(value):
    return json.dumps(_toml_safe(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _legacy_log_entry(entry):
    result = deepcopy(entry)  # Unknown extensions and original aliases are retained.
    for target, aliases in {'TIMESTAMP': ['TIME'], 'OPERATION': ['ACTION'], 'TOOL': ['ACTOR'],
                            'NOTES': ['MESSAGE'], 'PARAMS': ['PARAMETERS']}.items():
        if target not in result:
            for alias in aliases:
                if alias in result:
                    value = result[alias]
                    if target == 'PARAMS' and isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except json.JSONDecodeError:
                            value = {'RAW': value}
                    result[target] = value
                    break
    return result


def _normalized_log_entries(log):
    if log is None:
        return []
    if isinstance(log, dict) and isinstance(ci_get(log, 'ENTRIES'), list):
        values = ci_get(log, 'ENTRIES')
    elif isinstance(log, list):
        values = log
    else:
        values = [log]
    entries, previous = [], ''
    for index, value in enumerate(values):
        entry = _legacy_log_entry(value) if isinstance(value, dict) else {'LEGACY_VALUE': value}
        entry = _toml_safe(entry)
        if not entry.get('EVENT_ID'):
            entry['EVENT_ID'] = 'legacy-' + digest([previous, index, entry])
            entry['LEGACY_IMPORTED'] = True
        # No status, timestamp or historic hash is fabricated for old records.
        previous = entry['EVENT_ID']
        entries.append(entry)
    return entries


def log_entries(dataset):
    return _normalized_log_entries(ci_get(dataset.meta, 'LOG'))


def _set_records(dataset, entries, provenance=None):
    meta = deepcopy(dataset.meta)
    for key in list(meta):
        if str(key).upper() == 'LOG':
            del meta[key]
    if entries:
        meta['LOG'] = entries
    if provenance:
        for key in list(meta):
            if str(key).upper() == 'PROVENANCE':
                del meta[key]
        meta['PROVENANCE'] = provenance
    raw = dict(dataset.raw_sections)
    raw['META'] = dumps_toml(meta)
    return dataset.replace(meta=meta, raw_sections=raw)


def inherit_logs(dataset, *parents):
    """Merge all parents, keeping shared ancestry once and rejecting ID conflicts."""
    seen, entries = {}, []
    provenance = {'VERSION': SCHEMA_VERSION, 'CONTEXTS': {}, 'CONTROLS': {}}
    for source in [*parents, dataset]:
        if source is None:
            continue
        for entry in log_entries(source):
            key = entry['EVENT_ID']
            if key in seen:
                if canonical_json(seen[key]) != canonical_json(entry):
                    raise ValueError('Conflicting provenance EVENT_ID: ' + str(key))
                continue
            seen[key] = entry
            entries.append(entry)
        source_provenance = ci_get(source.meta, 'PROVENANCE', {})
        for section in ('CONTEXTS', 'CONTROLS'):
            for key, value in ci_get(source_provenance, section, {}).items():
                if key in provenance[section] and canonical_json(provenance[section][key]) != canonical_json(value):
                    raise ValueError('Conflicting provenance ' + section + ': ' + str(key))
                provenance[section][key] = deepcopy(value)
    return _set_records(dataset, entries, provenance if entries else None)


def _heads(entries):
    parents = {p for e in entries for p in e.get('PARENT_EVENT_IDS', [])}
    # Legacy records have only array order; link the final one as legacy ancestry.
    return [e['EVENT_ID'] for e in entries if e['EVENT_ID'] not in parents]


@lru_cache(maxsize=1)
def execution_environment():
    from . import __version__
    packages = {}
    for name in ('numpy', 'pandas', 'scipy', 'samplics', 'statsmodels', 'pint', 'matplotlib'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    root = Path(__file__).parent
    source = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        source.update(path.relative_to(root).as_posix().encode())
        source.update(path.read_bytes())
    return {'TOOL_VERSION': __version__, 'PYTHON': platform.python_version(), 'PACKAGES': packages,
            'IMPLEMENTATION_SHA256': source.hexdigest()}


@dataclass
class RunContext:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sequence: int = 0
    failure_directory: Any = None
    failure_files: list = field(default_factory=list)


@dataclass
class OperationContext:
    run: RunContext
    action: str
    inputs: list
    operation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec='milliseconds'))
    clock: float = field(default_factory=time.perf_counter)
    effective: dict = field(default_factory=dict)
    latest: Any = None


_RUN = ContextVar('tame_provenance_run', default=None)
_OPERATION = ContextVar('tame_provenance_operation', default=None)


@contextmanager
def run_scope(*, failure_directory=None):
    if _RUN.get() is not None:
        yield _RUN.get()
        return
    token = _RUN.set(RunContext(failure_directory=failure_directory))
    try:
        yield _RUN.get()
    finally:
        _RUN.reset(token)


@contextmanager
def operation_scope(action, inputs=(), *, parameters=None):
    with run_scope() as run:
        context = OperationContext(run, action, list(inputs), effective=deepcopy(parameters or {}))
        token = _OPERATION.set(context)
        try:
            yield context
        except BaseException as exc:
            if not hasattr(exc, 'audit_dataset') and context.inputs:
                try:
                    state = context.latest if context.latest is not None else context.inputs[0]
                    exc.audit_dataset = append_log_entry(state, action=action,
                        status='CANCELLED' if isinstance(exc, KeyboardInterrupt) else 'FAILED',
                        message=str(exc), parameters=context.effective,
                        error={'TYPE': type(exc).__name__, 'MESSAGE': str(exc)})
                except Exception as audit_error:
                    exc.audit_error = str(audit_error)
            if run.failure_directory is not None and not getattr(exc, 'audit_file', None):
                try:
                    path = write_failure_record(exc, run.failure_directory)
                    if path:
                        exc.audit_file = str(path)
                        run.failure_files.append(str(path))
                except Exception as audit_error:
                    exc.audit_error = str(audit_error)
            raise
        finally:
            _OPERATION.reset(token)


def record_effective_parameters(values):
    context = _OPERATION.get()
    if context:
        context.effective.update(deepcopy(values))
    return context is not None


def append_log_entry(dataset: TameDataset, *, action: str, message: str = '', parent: str = '', output: str = '',
                     parameters=None, warnings=None, actor='tametools', input_dataset=None, inputs=None,
                     status='SUCCEEDED', counts=None, effective_parameters=None, operation_output=None, error=None):
    from .provenance_audit import artifact_descriptor, control_snapshot, operation_artifacts
    if status not in STATUSES:
        raise ValueError('Unknown execution status: ' + str(status))
    context = _OPERATION.get()
    sources = list(inputs) if inputs is not None else ([input_dataset] if input_dataset is not None else
                                                     list(context.inputs) if context else [])
    dataset = inherit_logs(dataset, *sources)
    entries = log_entries(dataset)
    provenance = deepcopy(ci_get(dataset.meta, 'PROVENANCE', {'VERSION': SCHEMA_VERSION, 'CONTEXTS': {}, 'CONTROLS': {}}))
    run = context.run if context else (_RUN.get() or RunContext())
    run.sequence += 1
    environment = deepcopy(execution_environment())
    provenance.setdefault('CONTEXTS', {})[run.run_id] = environment
    finished = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    parent_ids = _heads(entries)
    lookup = {e['EVENT_ID']: e for e in entries}
    def describe(ds, name=''):
        descriptor = artifact_descriptor(ds, name=name)
        snapshot = control_snapshot(ds)
        serialized = canonical_json(snapshot)
        provenance.setdefault('CONTROLS', {})[descriptor['CONTROL_SHA256']] = {'CHUNKS': [{'TEXT': serialized[i:i+4000]} for i in range(0, len(serialized), 4000)]}
        return descriptor
    input_artifacts = [describe(ds, parent if len(sources) == 1 else '') for ds in sources]
    output_artifacts = [describe(dataset, output)] if status == 'SUCCEEDED' else []
    if operation_output is not None and status == 'SUCCEEDED':
        output_artifacts.extend(operation_artifacts(operation_output))
    entry = {'SCHEMA_VERSION': SCHEMA_VERSION, 'EVENT_ID': str(uuid.uuid4()), 'RUN_ID': run.run_id,
             'SEQUENCE': run.sequence, 'OPERATION_ID': context.operation_id if context else str(uuid.uuid4()),
             'TIMESTAMP': context.started if context else finished, 'FINISHED_AT': finished,
             'OPERATION': str(action).upper(), 'STATUS': status, 'TOOL': str(actor),
             'TOOL_VERSION': environment['TOOL_VERSION'], 'CONTEXT_SHA256': digest(environment),
             'HASH_SPEC': HASH_SPEC, 'PARENT_EVENT_IDS': parent_ids,
             'PARENT_HASHES': {i: lookup[i].get('EVENT_SHA256', digest(lookup[i])) for i in parent_ids},
             'INPUTS': input_artifacts, 'OUTPUTS': output_artifacts}
    if context:
        entry['DURATION_MS'] = round((time.perf_counter() - context.clock) * 1000, 3)
    if message:
        entry['NOTES'] = str(message)
    entry['SUMMARY'] = str(message or action)
    if parent:
        entry['PARENT'] = str(parent)
    if output:
        entry['OUTPUT'] = str(output)
    if parameters:
        entry['PARAMS'] = _toml_safe(parameters)
    effective = effective_parameters if effective_parameters is not None else (context.effective if context else parameters)
    if effective:
        entry['EFFECTIVE_PARAMS'] = _toml_safe(effective)
    totals = {}
    if sources:
        totals['INPUT_ROWS'] = sum(len(ds.df) for ds in sources)
        totals['INPUT_FILES'] = len(sources)
    if status == 'SUCCEEDED':
        totals.update(OUTPUT_ROWS=len(dataset.df), OUTPUT_COLUMNS=len(dataset.columns))
    if counts:
        totals.update(counts)
    entry['COUNTS'] = _toml_safe(totals)
    gaps = []
    for ds, observed in zip(sources, input_artifacts):
        previous = next((a for e in reversed(log_entries(ds)) for a in reversed(e.get('OUTPUTS', []))
                         if a.get('KIND') == 'dataset'), None)
        if previous is not None:
            changed = [k for k in ('DATA_SHA256', 'CONTROL_SHA256') if previous.get(k) != observed[k]]
            if changed:
                gaps.append({'NAME': observed['NAME'], 'CHANGED': changed, 'PREVIOUS': previous, 'OBSERVED': observed})
    if gaps:
        entry['UNRECORDED_INPUT_CHANGES'] = gaps
    if warnings:
        entry['WARNINGS'] = [str(w) for w in warnings]
    if error:
        entry['ERROR'] = _toml_safe(error)
    entry['EVENT_SHA256'] = digest(entry)
    result = _set_records(dataset, [*entries, entry], provenance)
    if context:
        context.latest = result
    return result


def failure_record(exc):
    ds = getattr(exc, 'audit_dataset', None)
    if ds is None:
        return None
    return {'VERSION': SCHEMA_VERSION, 'LOG': log_entries(ds), 'PROVENANCE': ci_get(ds.meta, 'PROVENANCE', {})}


def write_failure_record(exc, directory):
    record = failure_record(exc)
    if record is None:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('failed-' + str(uuid.uuid4()) + '.json')
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return path


def operation_boundary(action):
    """Give direct core API calls a scope; a pipeline/web scope groups nested logs."""
    from functools import wraps
    def decorate(function):
        @wraps(function)
        def wrapped(dataset, *args, **kwargs):
            if _OPERATION.get() is not None:
                return function(dataset, *args, **kwargs)
            with operation_scope(action, [dataset]):
                return function(dataset, *args, **kwargs)
        return wrapped
    return decorate


def tracked_input(function):
    from functools import wraps
    @wraps(function)
    def wrapped(*args, **kwargs):
        result = function(*args, **kwargs)
        context = _OPERATION.get()
        if context is not None and isinstance(result, TameDataset):
            if not any(ds.source_path == result.source_path for ds in context.inputs):
                context.inputs.append(result)
        return result
    return wrapped
