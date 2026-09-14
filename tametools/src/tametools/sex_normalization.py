"""Explicit sex recoding by source, preserving raw values and a replayable contract.

Numeric codes have no universal meaning. A mapping belongs to a source/code pair;
missing or unmapped pairs prevent application. Reading a TAME never executes this
contract implicitly. Call normalize_sex_by_source explicitly to apply/replay it.
"""
from collections import Counter
from copy import deepcopy

from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import ColumnSpec
from .provenance import append_log_entry, operation_boundary
from .sex import SEX_CANONICAL_VALUES, _sex_value_key, normalize_sex
from .tags import build_header


def _column(dataset, name, identifier):
    if identifier:
        matches = [c for c in dataset.columns if ci_get(dataset.column_metadata(c), 'ID') == identifier]
    else:
        matches = [c for c in dataset.columns if c.name == name]
    if len(matches) != 1:
        raise ValueError(f'성별 정규화 열을 하나로 식별할 수 없습니다: {identifier or name}')
    return matches[0]


def _config(dataset, options):
    cfg = deepcopy(options if options is not None else ci_get(dataset.meta, 'SEX_NORMALIZATION', {}))
    if not isinstance(cfg, dict):
        raise ValueError('성별 정규화 설정은 객체여야 합니다.')
    sex = _column(dataset, cfg.get('sexColumn'), cfg.get('SEX_ID'))
    source = _column(dataset, cfg.get('sourceColumn'), cfg.get('SOURCE_ID')) if cfg.get('sourceColumn') or cfg.get('SOURCE_ID') else None
    if source and source.name == sex.name:
        raise ValueError('출처 열과 성별 열은 서로 다른 열이어야 합니다.')
    mappings = {}
    for item in cfg.get('MAPPINGS', cfg.get('mappings', [])):
        source_value = item.get('SOURCE', item.get('source', ''))
        if not isinstance(source_value, str):
            raise ValueError('출처 값은 원문 문자열로 지정해 주세요.')
        if source_value in mappings:
            raise ValueError(f'출처 코드표가 중복되었습니다: {source_value}')
        values = {}
        for code, target in item.get('VALUES', item.get('values', {})).items():
            key = _sex_value_key(code)
            if target not in SEX_CANONICAL_VALUES:
                raise ValueError(f'성별 표준값을 선택해 주세요: {source_value} / {code}')
            if key in values and values[key] != target:
                raise ValueError(f'동일 코드에 서로 다른 성별을 지정했습니다: {source_value} / {code}')
            values[key] = target
        mappings[source_value] = values
    if not source and any(key != '' for key in mappings):
        raise ValueError('출처별 규칙에는 출처 열이 필요합니다.')
    return cfg, sex, source, mappings


def sex_normalization_preview(dataset, options):
    """Return observed pairs only; numeric mappings are deliberately not guessed."""
    cfg, sex, source, mappings = _config(dataset, options)
    counts = Counter()
    missing_sex = 0
    for pos, raw in enumerate(dataset.df[sex.name]):
        if cell_state(raw) != STATE_VALUE:
            missing_sex += 1
            continue
        source_raw = dataset.df[source.name].iloc[pos] if source else ''
        missing_source = bool(source and cell_state(source_raw) != STATE_VALUE)
        # Do not strip or numerically canonicalize source labels: distinct origins
        # (e.g. "01" and "1") must never share a mapping accidentally.
        source_value = str(source_raw) if not missing_source else ''
        counts[(source_value, _sex_value_key(raw), missing_source)] += 1
    rows = []
    for (origin, code, missing), count in counts.items():
        target = None if missing else mappings.get(origin, {}).get(code, normalize_sex(code))
        rows.append(dict(source=origin, code=code, count=count, target=target,
                         missingSource=missing, recognized=target is not None))
    return dict(rows=rows, rowCount=len(dataset.df), missingSex=missing_sex,
                unmappedCount=sum(r['count'] for r in rows if not r['recognized']),
                sexColumn=sex.name, sourceColumn=source.name if source else '',
                outputName=cfg.get('OUTPUT_NAME', cfg.get('outputName', sex.name + '_standard')))


@operation_boundary('NORMALIZE_SEX_BY_SOURCE')
def normalize_sex_by_source(dataset, options=None):
    """Create/update a derived SEX column using an explicit, persisted source map.

The stored COLUMN.ID bindings survive supported column renames/reordering.
Only a previously derived output with the same raw/source bindings may be
updated. Original sex and source cells, including missing states, are preserved.
"""
    cfg, sex, source, mappings = _config(dataset, options)
    preview = sex_normalization_preview(dataset, cfg)
    if preview['unmappedCount']:
        bad = [f"{r['source'] or '(출처 없음)'} / {r['code']} ({r['count']}행)" for r in preview['rows'] if not r['recognized']]
        raise ValueError('성별 코드표 미지정 또는 출처 결측: ' + ', '.join(bad[:10]) + '. 모든 코드의 의미를 확인한 뒤 적용해 주세요.')
    output_name = str(preview['outputName']).strip()
    if not output_name or any(c in output_name for c in '\r\n\t') or output_name in {sex.name, source.name if source else ''}:
        raise ValueError('원 성별·출처 열과 다른 표준 성별 열 이름을 지정해 주세요.')
    meta = deepcopy(dataset.meta)
    column_meta = deepcopy(ci_get(meta, 'COLUMN', {}))
    used = {ci_get(dataset.column_metadata(c), 'ID') for c in dataset.columns}

    def bind(column):
        entry = deepcopy(dataset.column_metadata(column))
        identifier = ci_get(entry, 'ID')
        if identifier and sum(ci_get(dataset.column_metadata(c), 'ID') == identifier for c in dataset.columns) != 1:
            raise ValueError(f'열 ID가 중복되어 코드표를 저장할 수 없습니다: {identifier}')
        if not identifier:
            base = column.name
            identifier = base
            index = 2
            while identifier in used:
                identifier = f'{base}_{index}'; index += 1
            entry['ID'] = identifier
            used.add(identifier)
        column_meta[column.name] = entry
        return identifier

    sex_id = bind(sex)
    source_id = bind(source) if source else ''
    previous = ci_get(meta, 'SEX_NORMALIZATION', {})
    existing_output = None
    if cfg.get('OUTPUT_ID'):
        existing_output = _column(dataset, None, cfg['OUTPUT_ID'])
        output_name = existing_output.name
    elif output_name in dataset.df.columns:
        existing_output = next(c for c in dataset.columns if c.name == output_name)
    if existing_output:
        output_id = ci_get(dataset.column_metadata(existing_output), 'ID')
        if (not output_id or previous.get('OUTPUT_ID') != output_id or previous.get('SEX_ID') != sex_id
                or previous.get('SOURCE_ID', '') != source_id):
            raise ValueError('기존 열을 덮어쓸 수 없습니다. 새 표준 성별 열 이름을 지정해 주세요.')
    else:
        output_id = output_name
        i = 2
        while output_id in used:
            output_id = f'{output_name}_{i}'; i += 1
    # Persist every observed lexical mapping, including recognized M/F, to make
    # replay independent of later changes to the built-in token dictionary.
    effective = {}
    for row in preview['rows']:
        effective.setdefault(row['source'], {})[row['code']] = row['target']
    for origin, values in mappings.items():
        effective.setdefault(origin, {}).update(values)
    converted = []
    for pos, raw in enumerate(dataset.df[sex.name]):
        if cell_state(raw) != STATE_VALUE:
            converted.append(raw)
        else:
            origin = str(dataset.df[source.name].iloc[pos]) if source else ''
            converted.append(effective[origin][_sex_value_key(raw)])
    frame = dataset.df.copy()
    frame[output_name] = converted
    columns = []
    for column in dataset.columns:
        if column.name == output_name:
            continue
        tags = column.tags
        if column.name == sex.name:
            # The derived column owns the SEX role; raw categories stay intact.
            tags = tuple(t for t in tags if not dataset.column_has_tag(ColumnSpec(t, t, [t]), 'SEX')) + ('RAW_SEX',)
            column_meta[column.name]['TAGS'] = list(tags)
        columns.append(ColumnSpec(build_header(column.name, tags), column.name, tags))
    tags = ('SEX', 'STR', 'NULLABLE')
    columns.append(ColumnSpec(build_header(output_name, tags), output_name, tags))
    column_meta[output_name] = dict(ID=output_id, LABEL='표준 성별', TAGS=list(tags),
                                    DERIVED_FROM=sex_id, NORMALIZATION='SEX_NORMALIZATION')
    meta['COLUMN'] = column_meta
    contract = dict(VERSION=1, SEX_ID=sex_id, SOURCE_ID=source_id, OUTPUT_ID=output_id,
                    OUTPUT_NAME=output_name, MAPPINGS=[dict(SOURCE=k, VALUES=v) for k, v in effective.items()],
                    UNMAPPED_POLICY='ERROR', RAW_VALUES_PRESERVED=True)
    meta['SEX_NORMALIZATION'] = contract
    ri = deepcopy(ci_get(meta, 'RI_EP28', {}))
    if not ci_get(ri, 'SEX_ID') or ci_get(ri, 'SEX_ID') == sex_id or ci_get(ri, 'SEX_ID') == previous.get('OUTPUT_ID'):
        ri['SEX_ID'] = output_id
        meta['RI_EP28'] = ri
    result = dataset.replace(df=frame[[c.name for c in columns]], columns=columns, meta=meta)
    return append_log_entry(result, action='NORMALIZE_SEX_BY_SOURCE', input_dataset=dataset,
        effective_parameters=contract, counts={'MAPPED_ROWS': len(frame)-preview['missingSex'],
        'MISSING_SEX_PRESERVED': preview['missingSex'], 'UNMAPPED_ROWS': 0, 'ADDED_COLUMNS': max(0,len(result.columns)-len(dataset.columns)),
        'UPDATED_COLUMNS': int(len(result.columns)==len(dataset.columns))},
        parameters=dict(contract=contract, rows=len(frame), mapped_rows=len(frame)-preview['missingSex'],
                        missing_sex_preserved=preview['missingSex'], raw_values_preserved=True),
        message='출처별 코드표로 표준 성별 열을 생성했습니다. 원 코드와 출처는 보존했습니다.')
