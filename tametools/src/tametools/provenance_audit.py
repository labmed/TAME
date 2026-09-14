"""Scoped history checks. A matching digest is not proof of clinical correctness."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

from .cellstate import cell_state, STATE_VALUE
from .config import ci_get
from .provenance import canonical_json, digest, HASH_SPEC, SCHEMA_VERSION, log_entries
from .toml_compat import loads as loads_toml


def _cell(value, numeric=False):
    state = cell_state(value)
    if state != STATE_VALUE:
        return [state, str(value) if state == 'WS' else '']
    text = str(value)
    if numeric:
        try:
            number = Decimal(text)
            if number.is_finite():
                parts = number.as_tuple()
                digits = ''.join(map(str, parts.digits))
                trimmed = digits.rstrip('0')
                text = f'{parts.sign}:{trimmed}:{parts.exponent + len(digits) - len(trimmed)}' if number else '0'
        except InvalidOperation:
            pass
    return [state, text]


def data_fingerprint(dataset):
    numeric = [dataset.column_has_any_tag(c, ('NUM', '<NUM>')) for c in dataset.columns]
    h = hashlib.sha256()
    h.update(canonical_json({'version': 2, 'columns': [c.name for c in dataset.columns]}).encode())
    for row in dataset.df.itertuples(index=False, name=None):
        h.update(b'\n')
        h.update(canonical_json([_cell(v, n) for v, n in zip(row, numeric)]).encode())
    return h.hexdigest()


def control_snapshot(dataset):
    meta = {k: deepcopy(v) for k, v in dataset.meta.items() if str(k).upper() not in {'LOG', 'PROVENANCE', 'INTEGRITY'}}
    # TAGS may move between headers and COLUMN on export; effective tags are below.
    for key in list(meta):
        if str(key).upper() == 'COLUMN' and isinstance(meta[key], dict):
            cleaned = {}
            for name, entry in meta[key].items():
                clean = {k: v for k, v in entry.items() if str(k).upper() != 'TAGS'} if isinstance(entry, dict) else entry
                if clean:
                    cleaned[name] = clean
            if cleaned:
                meta[key] = cleaned
            else:
                del meta[key]
    return {'VERSION': 2, 'META': meta, 'SCHEMA': dataset.schema, 'JOB': dataset.job,
            'COLUMNS': [{'NAME': c.name, 'TAGS': sorted(dataset.effective_column_tags(c))} for c in dataset.columns]}


def artifact_descriptor(dataset, *, name=''):
    data_hash, control_hash = data_fingerprint(dataset), digest(control_snapshot(dataset))
    return {'ARTIFACT_ID': digest([data_hash, control_hash]), 'KIND': 'dataset',
            'NAME': name or (Path(dataset.source_path).name if dataset.source_path else ''),
            'ROWS': len(dataset.df), 'COLUMNS': len(dataset.columns),
            'DATA_SHA256': data_hash, 'CONTROL_SHA256': control_hash, 'HASH_SPEC': HASH_SPEC}


def table_fingerprint(table):
    from pandas.api.types import is_numeric_dtype
    numeric = [is_numeric_dtype(table.iloc[:, i]) for i in range(len(table.columns))]
    return digest({'columns': list(map(str, table.columns)),
                   'rows': [[_cell(v, n) for v, n in zip(row, numeric)] for row in table.itertuples(index=False, name=None)]})


def operation_artifacts(output):
    artifacts = []
    tables = dict(output.tables or {})
    if output.table is not None and not any(output.table is t for t in tables.values()):
        tables['primary_table'] = output.table
    for name, table in tables.items():
        if table is not None:
            artifacts.append({'KIND': 'table', 'NAME': str(name), 'ROWS': len(table),
                              'COLUMNS': len(table.columns), 'TABLE_SHA256': table_fingerprint(table), 'HASH_SPEC': HASH_SPEC})
    if output.charts:
        artifacts.append({'KIND': 'charts', 'NAME': 'charts', 'COUNT': len(output.charts),
                          'SHA256': digest(output.charts), 'HASH_SPEC': HASH_SPEC})
    paths = [Path(p) for p in output.files or [] if Path(p).is_file()
             and Path(p).name != 'manifest.json' and Path(p).suffix.lower() != '.zip']
    if paths:
        base = Path(os.path.commonpath([str(p.resolve().parent) for p in paths]))
        for path in paths:
            artifacts.append({'KIND': 'file', 'NAME': path.resolve().relative_to(base).as_posix(),
                              'FILE_SHA256': hashlib.sha256(path.read_bytes()).hexdigest(), 'HASH_SPEC': 'file-bytes/sha256'})
    return artifacts


def comparison_controls(dataset):
    snapshot = control_snapshot(dataset)
    result = snapshot['META'].get('RI_EP28_RESULT')
    if isinstance(result, dict):
        result.pop('EFFECTIVE_INPUT_SHA256', None)
        if 'SETTINGS_JSON' in result:
            settings = json.loads(result['SETTINGS_JSON'])
            settings.pop('OUTPUT_DIR', None)
            settings.pop('REPORT_PATH', None)
            result['SETTINGS_JSON'] = canonical_json(settings)
    return snapshot


def result_fingerprint(result):
    """Compare all computed outputs, including analyses that keep DATA unchanged."""
    return digest({'dataset': {'data': data_fingerprint(result.final_dataset), 'controls': digest(comparison_controls(result.final_dataset))},
        'outputs': [{'name': o.name, 'artifacts': [a for a in operation_artifacts(o) if a['KIND'] != 'file'],
                     'warnings': o.warnings or [], 'issues': [str(i) for i in o.issues or []]} for o in result.outputs]})


def _verify_history(dataset, *, artifacts_dir=None):
    entries = log_entries(dataset)
    provenance = ci_get(dataset.meta, 'PROVENANCE', {})
    controls, contexts = ci_get(provenance, 'CONTROLS', {}), ci_get(provenance, 'CONTEXTS', {})
    errors, missing, legacy, seen, sequences = [], [], 0, {}, set()
    checked = 0
    for entry in entries:
        key = entry.get('EVENT_ID', '')
        if key in seen:
            errors.append(f'Duplicate EVENT_ID: {key}')
        if entry.get('SCHEMA_VERSION') != SCHEMA_VERSION:
            legacy += 1
            seen[key] = entry
            continue
        checked += 1
        if entry.get('UNRECORDED_INPUT_CHANGES'):
            missing.append(f'{key}: input changed after its preceding record; intervening edit not recorded')
        if entry.get('HASH_SPEC') != HASH_SPEC:
            errors.append(f'{key}: unsupported HASH_SPEC')
        if digest({k: v for k, v in entry.items() if k != 'EVENT_SHA256'}) != entry.get('EVENT_SHA256'):
            errors.append(f'{key}: event content changed')
        pair = (entry.get('RUN_ID'), entry.get('SEQUENCE'))
        if pair in sequences:
            errors.append(f'{key}: duplicate run sequence')
        sequences.add(pair)
        if entry.get('STATUS') not in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'SKIPPED'}:
            errors.append(f'{key}: missing or invalid execution status')
        for parent in entry.get('PARENT_EVENT_IDS', []):
            ancestor = seen.get(parent)
            if ancestor is None:
                errors.append(f'{key}: missing or out-of-order parent {parent}')
            elif entry.get('PARENT_HASHES', {}).get(parent) != ancestor.get('EVENT_SHA256', digest(ancestor)):
                errors.append(f'{key}: parent hash changed {parent}')
        context = contexts.get(entry.get('RUN_ID'))
        if context is None:
            missing.append(f'{key}: execution context missing')
        elif digest(context) != entry.get('CONTEXT_SHA256'):
            errors.append(f'{key}: execution context changed')
        for artifact in [*entry.get('INPUTS', []), *entry.get('OUTPUTS', [])]:
            if artifact.get('KIND') != 'dataset':
                continue
            sha = artifact.get('CONTROL_SHA256')
            snapshot = controls.get(sha)
            if snapshot is None:
                missing.append(f'{key}: control snapshot missing')
            else:
                try:
                    if digest(json.loads(snapshot.get('JSON') or ''.join(c['TEXT'] for c in snapshot['CHUNKS']))) != sha:
                        errors.append(f'{key}: control snapshot changed')
                except (KeyError, TypeError, json.JSONDecodeError):
                    errors.append(f'{key}: invalid control snapshot')
        seen[key] = entry
    current = artifact_descriptor(dataset)
    latest = next((a for e in reversed(entries) if e.get('SCHEMA_VERSION') == SCHEMA_VERSION
                   for a in reversed(e.get('OUTPUTS', [])) if a.get('KIND') == 'dataset'), None)
    def equality(field):
        return 'NOT_CHECKED' if latest is None else 'PASS' if current[field] == latest.get(field) else 'FAIL'
    file_artifacts = [a for e in entries for a in e.get('OUTPUTS', []) if a.get('KIND') == 'file']
    file_checks = []
    if artifacts_dir is not None:
        base = Path(artifacts_dir).resolve()
        for artifact in file_artifacts:
            path = (base / artifact['NAME']).resolve()
            if base not in path.parents:
                file_checks.append({'name': artifact['NAME'], 'status': 'FAIL', 'reason': 'path outside artifact directory'})
            elif not path.is_file():
                file_checks.append({'name': artifact['NAME'], 'status': 'NOT_CHECKED', 'reason': 'file unavailable'})
            else:
                equal = hashlib.sha256(path.read_bytes()).hexdigest() == artifact.get('FILE_SHA256')
                file_checks.append({'name': artifact['NAME'], 'status': 'PASS' if equal else 'FAIL'})
    file_status = 'NOT_CHECKED' if not file_checks else ('FAIL' if any(c['status'] == 'FAIL' for c in file_checks) else
        'PARTIAL' if any(c['status'] != 'PASS' for c in file_checks) else 'PASS')
    unsuccessful = [{'event_id': e['EVENT_ID'], 'operation': e.get('OPERATION'), 'status': e.get('STATUS')}
                    for e in entries if e.get('STATUS') in {'FAILED', 'CANCELLED', 'SKIPPED'}]
    checks = {'history': 'FAIL' if errors else 'PARTIAL' if checked and (legacy or missing) else 'PASS' if checked else 'NOT_CHECKED',
              'data': equality('DATA_SHA256'), 'controls': equality('CONTROL_SHA256'), 'artifacts': file_status,
              'rerun': 'NOT_CHECKED'}
    return {'version': 2, 'checks': checks, 'checked_events': checked, 'legacy_events': legacy, 'total_events': len(entries),
            'errors': errors, 'missing': missing, 'unsuccessful_steps': unsuccessful, 'files': file_checks,
            'scope': 'History checks recorded hashes and current data/controls. Original inputs, unavailable tables/files and past execution are not replayed.',
            'current': current}


def verify_history(dataset, *, artifacts_dir=None):
    try:
        return _verify_history(dataset, artifacts_dir=artifacts_dir)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        return {'version': 2, 'checks': {'history':'FAIL','data':'NOT_CHECKED','controls':'NOT_CHECKED',
                'artifacts':'NOT_CHECKED','rerun':'NOT_CHECKED'}, 'checked_events':0,'legacy_events':0,
                'total_events':len(log_entries(dataset)), 'errors':['Invalid LOG/PROVENANCE structure: '+str(exc)],
                'missing':[], 'unsuccessful_steps':[], 'files':[],
                'scope':'Malformed history is not repaired or treated as verified.', 'current':artifact_descriptor(dataset)}


LABELS = {'XLSX_MERGE_BINDING':'출처 시트 연결', 'EXPORT_TAME':'TAME 저장', 'EXPORT_XLSX':'Excel 저장',
          'EXPORT_PREPARATION':'저장 전 형식 정리', 'ANALYSIS_INPUT':'분석 입력 저장',
          'COMMAND:SAMPLE':'명령형 표본 추출', 'COMMAND:DESCRIBE':'자료 요약',
          'WORK:DESCRIBE':'자료 요약', 'WORK:VALIDATE':'태그·값 검증', 'WORK:EDA':'탐색 분석',
          'NORMALIZE_SEX_BY_SOURCE': '출처별 성별 정규화', 'FIX': '전처리 결과 생성', 'MERGE': '자료 병합',
          'IMPORT_XLSX': 'Excel 가져오기', 'WORK:SAMPLE': '표본 추출', 'SAMPLE': '표본 추출',
          'WORK:RI_EP28': 'EP28 참고구간 결과', 'REFERENCE_INTERVAL_EP28': 'EP28 참고구간 계산',
          'REFERENCE-INTERVAL': 'EP28 결과 생성', 'EDA': '탐색 분석', 'PLANNED-ANALYSIS': '저장된 분석 실행',
          'MANUAL_EDIT': '데이터·설정 편집', 'WORK:ANALYZE': '저장된 분석 실행', 'ANONYMIZE': '비식별화',
          'TAG_PLACEMENT': '태그 설정', 'PRESET': '프리셋 적용'}


def history_payload(dataset, *, artifacts_dir=None):
    entries = log_entries(dataset)
    groups, lookup = [], {}
    for index, entry in enumerate(entries, 1):
        key = entry.get('OPERATION_ID', entry['EVENT_ID'])
        if key not in lookup:
            group = {'id': key, 'index': len(groups) + 1, 'events': [], 'label': '', 'status': 'UNRECORDED',
                     'counts': {}, 'summary': '', 'warnings': [], 'mappings': []}
            lookup[key] = group
            groups.append(group)
        group = lookup[key]
        group['events'].append(entry)
        op = entry.get('OPERATION', 'LEGACY')
        group['label'] = LABELS.get(op, op)
        group['status'] = entry.get('STATUS', 'UNRECORDED')
        group['counts'].update(entry.get('COUNTS', {}) if isinstance(entry.get('COUNTS', {}), dict) else {})
        group['summary'] = entry.get('SUMMARY', entry.get('NOTES', '설명 미기록'))
        group['timestamp'] = entry.get('TIMESTAMP', '')
        group['version'] = entry.get('TOOL_VERSION', '미기록')
        group['warnings'] = list(dict.fromkeys([*group['warnings'], *(entry.get('WARNINGS', []) if isinstance(entry.get('WARNINGS', []), list) else [str(entry.get('WARNINGS'))])]))
        params = entry.get('PARAMS', {})
        if isinstance(params, dict):
            contract = params.get('contract', {})
            if isinstance(contract, dict) and contract.get('MAPPINGS'):
                group['mappings'] = contract['MAPPINGS']
                group['label'] = '출처별 성별 정규화'
                group['counts'].update({k.upper(): v for k, v in params.items() if k in ('mapped_rows', 'missing_sex_preserved')})
        # Keep the specific transformation label even when its web wrapper follows it.
        if group['mappings']:
            group['label'] = '출처별 성별 정규화'
            group['summary'] = '출처별 코드표로 표준 성별을 생성했습니다. 원 코드·출처·행을 보존했습니다.'
    return {'groups': groups, 'verification': verify_history(dataset, artifacts_dir=artifacts_dir),
            'eventCount': len(entries), 'operationCount': len(groups)}


def verify_pipeline_reproducible(run_callable):
    results = [run_callable(), run_callable()]
    hashes = [result_fingerprint(result) for result in results]
    reasons = []
    for index, result in enumerate(results, 1):
        incomplete = [o.name for o in result.outputs if o.status != 'SUCCEEDED']
        if incomplete:
            reasons.append(f'run {index}: unsuccessful steps: ' + ', '.join(incomplete))
        if any(o.issues for o in result.outputs):
            reasons.append(f'run {index}: validation issues require review')
    if len(set(hashes)) != 1:
        reasons.append('Outputs or effective controls differ; reuse recorded seeds and implementation versions.')
    return {'hashes': hashes, 'reproducible': len(set(hashes)) == 1 and not reasons, 'runs': 2, 'reasons': reasons}
