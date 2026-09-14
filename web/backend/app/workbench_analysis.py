"""Common workbench analysis contracts, independent of a particular example dataset."""
from copy import deepcopy
import importlib.util
import json
from hashlib import sha256
from pathlib import Path
import secrets
import sys
import zipfile

from tametools.analysis import parse_comparator_number
from tametools.cellstate import STATE_VALUE, cell_state
from tametools.config import ci_get
from tametools.plugin_base import run_plugin
from tametools.provenance import append_log_entry
from .provenance_ui import web_operation


def dependency_message(exc):
    missing = [name for name in ('pyreadstat', 'samplics') if importlib.util.find_spec(name) is None]
    name = getattr(exc, 'name', None)
    if name and name not in missing:
        missing.append(name)
    detail = ', '.join(missing) or str(exc)
    return (f'필요한 구성요소를 불러오지 못했습니다: {detail}. '
            f'실행 중인 Python: {sys.executable}. 저장소 루트에서 '
            f'"{sys.executable}" -m pip install -e "./tametools[analysis,nhanes,web,report]" '
            '명령을 실행한 뒤 웹앱을 다시 시작해 주세요.')


def require_example_dependencies():
    missing = [name for name in ('pyreadstat', 'samplics') if importlib.util.find_spec(name) is None]
    if missing:
        raise ModuleNotFoundError('누락된 패키지: ' + ', '.join(missing), name=missing[0])


def data_role(dataset):
    if ci_get(ci_get(dataset.meta, 'WEB_WORKBENCH', {}), 'KIND', '') == 'result':
        return 'result'
    if any(ci_get(dataset.meta, key, None) is not None for key in ('EDA', 'RI_EP28_RESULT', 'ANALYSIS_RESULT')):
        return 'result'
    return 'input'


def data_context(dataset):
    survey = bool(ci_get(dataset.meta, 'SURVEY', {}))
    nhanes = ci_get(dataset.meta, 'NHANES_WORKFLOW', {})
    warnings = []
    if survey:
        warnings.append('조사 표본 자료입니다. 단순 탐색 요약은 비가중 관측값이며 모집단 추정치가 아닙니다. '
                        '저장된 분석 실행은 META의 가중치·층·집락과 대상자 조건을 적용합니다.')
    if nhanes:
        warnings.append('NHANES는 일반 인구 조사로 건강한 참고인 자료가 아닙니다. '
                        '참고구간 기능은 사용성 시험과 탐색 목적으로만 사용하고 임상 기준으로 채택하지 마세요.')
        warnings.append('연령 80은 80세 이상을 뜻합니다. 검출한계 미만 여부는 공개 플래그가 있는 검사에서만 판정합니다.')
    return dict(survey=survey, hasAnalysisPlan=bool(ci_get(dataset.meta, 'ANALYSIS_PLAN', {})), warnings=warnings)


def require_input(dataset):
    if data_role(dataset) == 'result':
        raise ValueError('분석 결과표에는 새 참고구간을 계산할 수 없습니다. 작업 이력에서 원자료 또는 전처리 자료를 입력으로 선택해 주세요.')
    if not dataset.columns_with_tag('RESULT'):
        raise ValueError('분석할 RESULT 열이 없습니다. 검사값 열에 RESULT와 NUM 또는 <NUM> 태그를 지정해 주세요.')


def bind_reference_input(dataset):
    """Bind existing declared UNIT/TESTNAME roles; never guess a unit or subject ID."""
    require_input(dataset)
    meta = deepcopy(dataset.meta)
    column_meta = deepcopy(ci_get(meta, 'COLUMN', {}))
    used = {str(ci_get(entry, 'ID')) for entry in column_meta.values() if isinstance(entry, dict) and ci_get(entry, 'ID')}
    bindings = []
    for column in dataset.columns:
        entry = deepcopy(dataset.column_metadata(column))
        if not ci_get(entry, 'ID'):
            identifier = column.name
            suffix = 2
            while identifier in used:
                identifier = f'{column.name}_{suffix}'; suffix += 1
            entry['ID'] = identifier; used.add(identifier)
            bindings.append(f'{column.name}: COLUMN.ID={identifier}')
        column_meta[column.name] = entry
    unit_columns = dataset.columns_with_tag('UNIT')
    for column in dataset.columns_with_tag('RESULT'):
        entry = column_meta[column.name]
        pivot = ci_get(entry, 'PIVOT_CONTEXT', {})
        if not ci_get(entry, 'UNIT') and not ci_get(entry, 'UNIT_ID'):
            if ci_get(pivot, 'UNIT'):
                entry['UNIT'] = ci_get(pivot, 'UNIT')
                bindings.append(f'{column.name}: PIVOT_CONTEXT.UNIT 바인딩')
            elif len(unit_columns) == 1:
                entry['UNIT_ID'] = column_meta[unit_columns[0].name]['ID']
                bindings.append(f'{column.name}: UNIT_ID={entry["UNIT_ID"]}')
            else:
                raise ValueError(f'{column.name}: 단위를 확인할 수 없습니다. 열의 UNIT 메타데이터 또는 단위를 담은 UNIT 태그 열을 지정해 주세요.')
    meta['COLUMN'] = column_meta
    prepared = dataset.replace(meta=meta)
    if bindings:
        prepared = append_log_entry(prepared, action='RI-INPUT-BINDING', parameters={'bindings': bindings},
                                    message='기존 열·태그의 식별자와 단위 연결을 분석 입력에 기록했습니다.')
    return prepared


@web_operation('REFERENCE_INTERVAL_EP28')
def reference_analysis(payload, options=None, workspace=None):
    from .tametools_bridge import dataset_from_payload, dataset_payload, derived_filename, frame_records, payload_filename
    dataset = bind_reference_input(dataset_from_payload(payload, standardize=False))
    cfg = deepcopy(options or {})
    if any(str(k).upper() in {'OUTPUT_DIR', 'REPORT_PATH'} for k in cfg):
        raise ValueError('보고서는 웹앱의 작업 폴더에 저장됩니다. OUTPUT_DIR/REPORT_PATH 옵션은 제거해 주세요.')
    cfg.setdefault('OUTLIER_METHOD', ci_get(ci_get(dataset.meta, 'RI_EP28', {}), 'OUTLIER_METHOD', 'TUKEY'))
    cfg.setdefault('OUTLIER_ACTION', ci_get(ci_get(dataset.meta, 'RI_EP28', {}), 'OUTLIER_ACTION', 'FLAG'))
    cfg.setdefault('POPULATION', deepcopy(ci_get(ci_get(dataset.meta, 'RI_EP28', {}), 'POPULATION', {})))
    # Survey records are examples, not independently screened reference individuals.
    if ci_get(dataset.meta, 'SURVEY', None) is not None:
        cfg['POPULATION']['REFERENCE_INDIVIDUALS_CONFIRMED'] = False
    report_id = secrets.token_urlsafe(18) if workspace else None
    folder = Path(workspace)/'analysis_reports'/report_id if workspace else None
    if folder:
        cfg.update(OUTPUT_DIR=str(folder), PLOT_MAX_GROUPS=6)
    output = run_plugin(dataset, 'RI_EP28', dataset.meta, 'RI_EP28', cfg)
    if output is None or output.dataset is None:
        raise ValueError('RI_EP28 플러그인을 불러오지 못했습니다. tametools 0.4.0 설치와 Python 실행 경로를 확인해 주세요.')
    if output.table is None or output.table.empty:
        raise ValueError('분석 가능한 검사값이 없습니다. RESULT 태그, 대상자 조건, 결측값과 부등호 처리 정책을 확인해 주세요.')
    warnings = [*data_context(dataset)['warnings'], *(output.warnings or [])]
    name = derived_filename(payload, 'ri_ep28')
    meta = deepcopy(output.dataset.meta)
    meta['WEB_WORKBENCH'] = dict(KIND='result', ANALYSIS='RI_EP28', SOURCE=payload_filename(payload), REPORT_ID=report_id or '')
    result = append_log_entry(output.dataset.replace(meta=meta), action='reference-interval',
        parent=payload_filename(payload), output=name, parameters={'plugin':'RI_EP28','options':cfg}, warnings=warnings, operation_output=output,
        message=f'EP28 분석 결과 {len(output.table)}행과 상세 산출물을 생성했습니다.')
    response = dataset_payload(result, filename=name, warnings=warnings)
    effective = ci_get(result.meta, 'RI_EP28_RESULT', {})
    settings = json.loads(ci_get(effective, 'SETTINGS_JSON', '{}'))
    response.update(resultType='reference_interval', analysisTables={}, analysisTableFiles={},
                    analysisSource=payload_filename(payload), analysisSettings={k:v for k,v in settings.items() if k not in {'OUTPUT_DIR','REPORT_PATH'}},
                    testLabels=ci_get(effective, 'TEST_LABELS', {}))
    table_index = {}
    for key, table in output.tables.items():
        if folder and (key in {'input_audit','group_membership'} or (len(table) > 200 and key != 'reference_intervals')):
            table_index[key] = dict(rowCount=len(table), columns=list(table.columns))
            response['analysisTableFiles'][key] = dict(**table_index[key],
                pageUrl=f'/api/analysis-tables/{report_id}/{key}', downloadUrl=f'/api/analysis-tables/{report_id}/{key}/csv')
        else:
            response['analysisTables'][key] = frame_records(table)
    if folder:
        from tametools.io import write_tame
        # The final downloaded result includes the complete web/input chain.
        write_tame(folder/'workbench_result.tame', result)
        input_meta = deepcopy(dataset.meta)
        input_meta.setdefault('RI_EP28', {}).update({k:v for k,v in cfg.items() if k not in {'OUTPUT_DIR','REPORT_PATH'}})
        input_meta['RI_EP28'].pop('OUTPUT_DIR', None)
        input_meta['RI_EP28'].pop('REPORT_PATH', None)
        prepared_input = append_log_entry(dataset.replace(meta=input_meta), action='ANALYSIS_INPUT', input_dataset=dataset,
            parameters={'RI_EP28':input_meta['RI_EP28']}, message='보고서 재검증용 분석 입력과 적용 옵션을 저장했습니다.')
        write_tame(folder/'analysis_input.tame', prepared_input, standardize_sex_values=False)
        from tametools.provenance_audit import history_payload
        history = history_payload(result)
        (folder/'analysis_history.json').write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
        import csv
        with (folder/'analysis_history.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['단계','작업','상태','입력 행','출력 행','매핑 행','출처별 코드표','설명'])
            for group in history['groups']:
                values = [group['index'],group['label'],group['status'],group['counts'].get('INPUT_ROWS',''),
                    group['counts'].get('OUTPUT_ROWS',''),group['counts'].get('MAPPED_ROWS',''),
                    '; '.join(m['SOURCE']+': '+', '.join(k+'→'+v for k,v in m['VALUES'].items()) for m in group['mappings']),group['summary']]
                writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in values])
        (folder/'table_index.json').write_text(json.dumps(table_index,ensure_ascii=False),encoding='utf-8')
        # Include the final web input/result and paging index in the downloadable manifest.
        manifest_path = folder/'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files'] = [dict(path=p.relative_to(folder).as_posix(), sha256=sha256(p.read_bytes()).hexdigest())
            for p in sorted(folder.rglob('*')) if p.is_file() and p != manifest_path and p.name != 'reference_interval_report.zip']
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        archive = folder/'reference_interval_report.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in sorted(folder.rglob('*')):
                if p.is_file() and p != archive:z.write(p,p.relative_to(folder).as_posix())
        response['reportFiles'] = [dict(label=label, url=f'/api/analysis-reports/{report_id}/{file}', filename=file)
            for label,file in [('Word 보고서','reference_interval_report_ko.docx'),('전체 보고서 ZIP','reference_interval_report.zip')]]
    return response


@web_operation('PLANNED_ANALYSIS')
def planned_analysis(payload):
    from .tametools_bridge import dataset_from_payload, dataset_payload, derived_filename, frame_records, payload_filename
    from tametools.planned_analysis import analyze_dataset
    dataset = dataset_from_payload(payload, standardize=False)
    require_input(dataset)
    if not ci_get(dataset.meta, 'ANALYSIS_PLAN', None):
        raise ValueError('저장된 ANALYSIS_PLAN이 없습니다. 분석 설정을 META에 선언해 주세요.')
    output = analyze_dataset(dataset)
    meta = deepcopy(output.dataset.meta)
    meta['WEB_WORKBENCH'] = dict(KIND='result', ANALYSIS='ANALYSIS_PLAN', SOURCE=payload_filename(payload))
    name = derived_filename(payload, 'planned_analysis')
    result = append_log_entry(output.dataset.replace(meta=meta), action='planned-analysis', parent=payload_filename(payload),
        output=name, parameters={'ANALYSIS_PLAN':ci_get(dataset.meta,'ANALYSIS_PLAN')}, warnings=output.warnings or [],
        operation_output=output, effective_parameters=ci_get(dataset.meta,'ANALYSIS_PLAN'),
        message=f'저장된 분석 계획을 실행했습니다. 입력 {len(dataset.df)}행, 결과표 {len(output.tables)}개.')
    response = dataset_payload(result, filename=name, warnings=[*data_context(dataset)['warnings'], *(output.warnings or [])])
    response.update(resultType='planned_analysis', analysisTables={k:frame_records(v) for k,v in output.tables.items()},
                    testLabels={str(ci_get(dataset.column_metadata(c),'ID',c.name)):str(ci_get(dataset.column_metadata(c),'LABEL',c.name)) for c in dataset.columns_with_tag('RESULT')})
    return response


def comparator_policy(dataset, policy):
    if policy not in {'exclude', 'value'}:
        raise ValueError('부등호 처리 정책을 선택해 주세요: 분석에서 제외 또는 경계값으로 분석. 행과 원문은 보존됩니다.')
    meta = deepcopy(dataset.meta);meta.setdefault('SETTINGS', {})['CRR'] = 'DELETE' if policy=='exclude' else 'VALUE'
    changed = 0; columns = []
    from tametools.models import ColumnSpec
    from tametools.tags import build_header
    for column in dataset.columns:
        tags=list(column.tags)
        if dataset.column_has_any_tag(column, ('NUM','<NUM>')):
            count=sum(1 for value in dataset.df[column.name] if cell_state(value)==STATE_VALUE
                and (parsed:=parse_comparator_number(str(value).strip())) and parsed[0] not in {'','='})
            changed+=count
            if count:
                tags=[tag for tag in tags if tag!='NUM']
                if '<NUM>' not in tags:tags.append('<NUM>')
                entry=meta.setdefault('COLUMN',{}).setdefault(column.name,{})
                if 'TAGS' in entry:entry['TAGS']=tags
        columns.append(ColumnSpec(build_header(column.name,tags),column.name,tuple(tags)))
    return dataset.replace(columns=columns,meta=meta),changed
