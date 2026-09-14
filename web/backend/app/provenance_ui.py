"""Web operation boundaries, explicit edit commits, and downloadable failure records."""
from copy import deepcopy
from functools import wraps
import json

from tametools.provenance import append_log_entry, operation_scope, log_entries
from tametools.provenance_audit import artifact_descriptor, verify_history
from tametools.toml_compat import dumps


def recorded_dataset(payload):
    from .tametools_bridge import dataset_from_payload
    dataset = dataset_from_payload(payload, standardize=False)
    baseline = payload.get('auditBaseline')
    if not isinstance(baseline, dict):
        return dataset
    current = artifact_descriptor(dataset)
    changed = [key for key in ('DATA_SHA256', 'CONTROL_SHA256') if baseline.get(key) and baseline[key] != current[key]]
    checks = verify_history(dataset)
    if checks['checks']['history'] == 'FAIL':
        raise ValueError('LOG 또는 연결 정보가 변경되었습니다. 분석 이력의 기록 검증에서 오류를 확인해 주세요.')
    if not changed:
        return dataset
    # The baseline is the last server response. It is not an authenticated external source.
    return append_log_entry(dataset, action='MANUAL_EDIT',
        message='작업실에서 데이터 또는 설정을 편집했습니다. 편집 전후 지문을 기록했습니다.',
        parameters={'changed': changed, 'before': baseline, 'after': current,
                    'recording': 'User edits committed at analysis, refresh or download; original cells are not reconstructed from hashes.'},
        counts={'INPUT_ROWS': baseline.get('ROWS', len(dataset.df)), 'OUTPUT_ROWS': len(dataset.df)})


def web_operation(action):
    def decorate(function):
        @wraps(function)
        def wrapped(payload, *args, **kwargs):
            dataset = recorded_dataset(payload)
            prepared = dict(payload, metaText=dumps(dataset.meta), auditBaseline=artifact_descriptor(dataset))
            parameters = {'options': deepcopy(args[0])} if args and isinstance(args[0], dict) else {}
            if payload.get('sexBinaryMap'):
                parameters['sexBinaryMap'] = deepcopy(payload['sexBinaryMap'])
            with operation_scope(action, [dataset], parameters=parameters):
                return function(prepared, *args, **kwargs)
        return wrapped
    return decorate
