"""Reviewed, single-cycle NHANES chemistry recipes for the guided workbench."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from tametools.analysis_contract import derive_censored_results
from tametools.cellstate import NULL
from tametools.io import write_tame
from tametools.models import ColumnSpec, TameDataset
from tametools.planned_analysis import analyze_dataset

BASE = 'https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/'
WEIGHT_GUIDE = 'https://wwwn.cdc.gov/nchs/nhanes/tutorials/weighting.aspx'
RECIPES = {
    '2021-2023': dict(year=2021, suffix='L', survey_code=12, label='2021–2023년 (2021.8–2023.8)',
                      weight='WTPH2YR', weight_label='채혈 검사 가중치', instrument='Roche Cobas 8000'),
    '2017-2018': dict(year=2017, suffix='J', survey_code=10, label='2017–2018년',
                      weight='WTMEC2YR', weight_label='검진 가중치', instrument='Roche Cobas 6000'),
}
VARIABLES = [
    dict(id='ALT', name='ALT', label='ALT · 알라닌 아미노전이효소', variable='LBXSATSI', unit='U/L',
         description='간 효소 수치의 분포를 살펴봅니다.', flag='LBDSATLC', limit=3., recommended=True),
    dict(id='CREATININE', name='크레아티닌', label='크레아티닌', variable='LBXSCR', unit='mg/dL',
         description='신장 관련 검사값을 연령·성별로 비교합니다.', recommended=True),
    dict(id='ALBUMIN', name='알부민', label='알부민', variable='LBXSAL', unit='g/dL',
         description='혈청 단백질의 분포와 평균을 확인합니다.', recommended=True),
    dict(id='AST', name='AST', label='AST · 아스파르테이트 아미노전이효소', variable='LBXSASSI', unit='U/L',
         description='다른 간 효소와 분포를 함께 확인합니다.', recommended=False),
    dict(id='URIC_ACID', name='요산', label='요산', variable='LBXSUA', unit='mg/dL',
         description='요산 수치의 분포와 성별 차이를 살펴봅니다.', recommended=False),
    dict(id='CALCIUM', name='칼슘', label='총 칼슘', variable='LBXSCA', unit='mg/dL',
         description='혈청 총 칼슘의 분포를 확인합니다.', recommended=False),
]
BY_ID = {v['id']: v for v in VARIABLES}
AGE_GROUPS = {
    'adults': dict(label='20세 이상 성인', minimum=20),
    'eligible': dict(label='검사 대상 전체 · 12세 이상', minimum=12),
    '20_39': dict(label='20–39세', minimum=20, maximum_exclusive=40),
    '40_59': dict(label='40–59세', minimum=40, maximum_exclusive=60),
    '60_plus': dict(label='60세 이상 · 80세 이상 포함', minimum=60),
}
CAUTIONS = [
    'NHANES는 미국의 일반 인구 조사입니다. 건강한 참고인만 선별한 자료가 아니므로 이 결과를 임상 참고구간이나 진단 기준으로 사용하지 않습니다.',
    '조사 가중치와 층·집락을 적용한 평균 및 95% 신뢰구간을 제공합니다. 중앙값·사분위수·분포도는 분석에 포함된 실제 관측값의 비가중 요약입니다.',
    '연령·성별은 도메인으로 분석하며 전체 표본설계를 유지합니다. 표에 표시한 인원은 유효 검사값을 가진 실제 참여자 수이며 미국 인구 수가 아닙니다.',
    '기본 설정은 공개된 검사값을 유지합니다. 이상치를 자동 제거하지 않고, 결측치는 각 검사항목별로 제외하며 추가 결측 가중치 보정은 하지 않습니다.',
    '검출한계 표시가 제공된 항목만 미만 여부를 구분합니다. 표시가 없는 검사값의 미만 여부를 수치만으로 추정하지 않습니다.',
    '80은 정확한 80세가 아니라 80세 이상을 뜻합니다. 조사 시기와 측정 장비가 다른 자료를 자동 합산하거나 연도 간 추세로 해석하지 않습니다.',
    '성별 평균 차이는 연령 등 다른 요인을 보정한 효과가 아닙니다. 신뢰구간의 겹침만으로 유의성을 판정하지 않습니다.',
]


def recipe(cycle):
    if cycle not in RECIPES:
        raise ValueError('지원하는 조사 시기를 선택해 주세요.')
    return RECIPES[cycle]


def file_ids(cycle):
    suffix = recipe(cycle)['suffix']
    return [f'DEMO_{suffix}', f'BIOPRO_{suffix}']


def sources(cycle):
    r = recipe(cycle)
    return [dict(name='참여자·표본설계' if name.startswith('DEMO') else '임상화학 검사',
                 file=name, url=f'{BASE}{r["year"]}/DataFiles/{name}.htm') for name in file_ids(cycle)]


def catalog():
    return dict(default_cycle='2021-2023', cycles=[dict(id=k, **v, sources=sources(k)) for k,v in RECIPES.items()],
                variables=deepcopy(VARIABLES), age_groups=[dict(id=k, **v) for k,v in AGE_GROUPS.items()],
                defaults=dict(variables=['ALT','CREATININE','ALBUMIN'], age_group='adults', by_sex=True, policy='RELEASED'),
                weight_guide=WEIGHT_GUIDE)


def _cell(value):
    if pd.isna(value): return NULL
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def prepare_dataset(directory, cycle):
    """Retain every DEMO row and explicit released/flag/derived value bindings."""
    import pyreadstat
    from tametools.analysis import validate_dataset
    r = recipe(cycle)
    variables = deepcopy(VARIABLES)
    if cycle == '2021-2023':
        for v in variables:
            v.pop('flag', None)
            if v['unit'] == 'U/L': v['unit'] = 'IU/L'
    directory = Path(directory)
    demo_name, bio_name = file_ids(cycle)
    # Recent XPORT labels contain Windows-1252 punctuation; the numeric data are unchanged.
    demo, _ = pyreadstat.read_xport(str(directory/'sources'/f'{demo_name}.xpt'), encoding='WINDOWS-1252')
    bio, _ = pyreadstat.read_xport(str(directory/'sources'/f'{bio_name}.xpt'), encoding='WINDOWS-1252')
    required_demo = {'SEQN','SDDSRVYR','RIDAGEYR','RIAGENDR','SDMVSTRA','SDMVPSU'}
    if r['weight']=='WTMEC2YR': required_demo.add('WTMEC2YR')
    required_bio = {'SEQN',*[v['variable'] for v in variables],*[v['flag'] for v in variables if v.get('flag')]}
    if r['weight']=='WTPH2YR': required_bio.add('WTPH2YR')
    if not required_demo <= set(demo) or not required_bio <= set(bio):
        raise ValueError('공식 파일의 필수 변수가 예상과 다릅니다. 자료 설명서를 확인해 주세요.')
    for name,frame in [('DEMO',demo),('BIOPRO',bio)]:
        ids = frame.SEQN
        if ids.isna().any() or ids.duplicated().any() or not np.isfinite(ids).all() or not ids.mod(1).eq(0).all():
            raise ValueError(f'{name}의 참여자 ID가 누락되거나 중복됩니다. 자동 병합을 중단했습니다.')
    if not demo.SDDSRVYR.eq(r['survey_code']).all():
        raise ValueError('선택한 조사 시기와 인구자료의 조사 코드가 다릅니다.')
    if not set(bio.SEQN) <= set(demo.SEQN):
        raise ValueError('검사자료에 인구자료와 연결되지 않는 참여자가 있습니다.')
    if r['weight']=='WTPH2YR' and bio[r['weight']].isna().any():
        raise ValueError('검사자료의 채혈 가중치에 결측이 있습니다.')
    joined = demo[sorted(required_demo)].merge(bio[sorted(required_bio)], on='SEQN', how='left', validate='one_to_one', indicator=True)
    if r['weight']=='WTPH2YR':
        # Only records absent from this laboratory component get zero phlebotomy weight.
        joined.loc[joined['_merge'].eq('left_only'),r['weight']] = 0
    weight = joined[r['weight']]
    if weight.isna().any() or not np.isfinite(weight).all() or weight.lt(0).any() or not weight.gt(0).any():
        raise ValueError('조사 가중치가 유효하지 않아 인구 추정을 진행할 수 없습니다.')
    if not joined.RIAGENDR.dropna().isin([1,2]).all():
        raise ValueError('성별 코드가 공식 설명의 1/2 범위와 다릅니다.')
    frame = pd.DataFrame({
        'SEQN':joined.SEQN.map(_cell), 'age':joined.RIDAGEYR.map(_cell),
        'sex':joined.RIAGENDR.map({1.:'male',2.:'female'}).where(joined.RIAGENDR.notna(),NULL),
        'weight':weight.map(_cell), 'stratum':joined.SDMVSTRA.map(_cell), 'psu':joined.SDMVPSU.map(_cell),
        'chemistry_eligible':joined.RIDAGEYR.ge(12).map(lambda v:'1' if v else '0'),
    }, dtype=object)
    tags = {'SEQN':['ID','STR'], 'age':['NUM','RAW_AGE','NULLABLE'], 'sex':['SEX','CATEGORY','NULLABLE'],
            'weight':['NUM','SURVEY_WEIGHT'], 'stratum':['CATEGORY','NULLABLE'], 'psu':['CATEGORY','NULLABLE'],
            'chemistry_eligible':['NUM']}
    columns = {c:dict(ID=c) for c in frame}
    columns['age'].update(UNIT='a', TOP_CODE_VALUE=80, LABEL='연령 (80 = 80세 이상)', SOURCE_VARIABLE='RIDAGEYR')
    columns['sex'].update(LABEL='성별', SOURCE_VARIABLE='RIAGENDR', CODE_MAPPING='1=male; 2=female')
    columns['weight'].update(SOURCE_VARIABLE=r['weight'], LABEL=r['weight_label'])
    columns['stratum']['SOURCE_VARIABLE']='SDMVSTRA'
    columns['psu']['SOURCE_VARIABLE']='SDMVPSU'
    for v in variables:
        identifier = v['id']
        raw = joined[v['variable']].map(_cell)
        frame[identifier] = raw
        tags[identifier]=['RESULT','NUM','NULLABLE']
        columns[identifier]=dict(ID=identifier, UNIT=v['unit'], LABEL=v['name'],
            SOURCE_VARIABLE=v['variable'], SOURCE_FILE=f'{bio_name}.xpt', ELIGIBLE_ID='chemistry_eligible',
            MEASUREMENT=dict(COMPONENT=identifier, SPECIMEN='serum', METHOD=r['instrument'],
                             TIME='Pt', SCALE='Qn', PROPERTY='catalytic_activity_concentration' if v['unit'] in {'U/L','IU/L'} else 'mass_concentration'))
        if v.get('flag'):
            frame[identifier+'_released']=raw
            frame[identifier+'_flag']=joined[v['flag']].map(_cell)
            tags[identifier]=['RESULT','<NUM>','NULLABLE']
            tags[identifier+'_released']=['NUM','RAW_RESULT','NULLABLE']
            tags[identifier+'_flag']=['NUM','NULLABLE']
            columns[identifier+'_released']=dict(ID=identifier+'_released',UNIT=v['unit'],SOURCE_VARIABLE=v['variable'])
            columns[identifier+'_flag']=dict(ID=identifier+'_flag',SOURCE_VARIABLE=v['flag'])
            columns[identifier]['CENSORING']=dict(SOURCE_ID=identifier+'_released', FLAG_ID=identifier+'_flag',
                                                 LIMIT=v['limit'], BELOW_CODE=1, OBSERVED_CODE=0)
    manifest = json.loads((directory/'source_manifest.json').read_text(encoding='utf-8'))
    meta = dict(COLUMN=columns, ANALYSIS_CONTRACT=dict(VERSION=1),
        OBSERVATION=dict(VERSION=1, ROW_UNIT='person', KEY_IDS=['SEQN'], SUBJECT_ID='SEQN', REPEAT_POLICY='ERROR'),
        SURVEY=dict(DESIGN='STRATIFIED_CLUSTER_WR',WEIGHT_ID='weight',STRATUM_ID='stratum',PSU_ID='psu',EXPECTED_ROWS=len(frame)),
        NHANES_WORKFLOW=dict(VERSION=1,CYCLE=cycle,WEIGHT_VARIABLE=r['weight'], SOURCES=sources(cycle),
                            SOURCE_MANIFEST=manifest, JOIN='DEMO left join BIOPRO on unique SEQN; full rows retained',
                            SEX_MAPPING='RIAGENDR 1=male, 2=female', AGE_TOP_CODE='80 means 80 years or older', XPT_ENCODING='WINDOWS-1252'),
        ANALYSIS_PLAN=make_plan(dict(variables=['ALT','CREATININE','ALBUMIN'],age_group='adults',by_sex=True,policy='RELEASED')))
    dataset = TameDataset(frame,[ColumnSpec(c,c,tags[c]) for c in frame],meta)
    dataset = derive_censored_results(dataset)
    errors = [i for i in validate_dataset(dataset).issues if i.severity=='error']
    if errors: raise ValueError('자료 검증을 통과하지 못했습니다: '+errors[0].message)
    info = dict(cycle=cycle, label=r['label'], people=len(demo), laboratory_rows=len(bio),
                linked_people=len(bio), unmatched_laboratory_rows=0, positive_weight_people=int(weight.gt(0).sum()),
                adult_people=int(joined.RIDAGEYR.ge(20).sum()), weight_variable=r['weight'],
                weight_label=r['weight_label'], sources=sources(cycle),
                censoring_available=any(v.get('flag') for v in variables),
                variables=[dict(**v, available_n=int(joined[v['variable']].notna().sum())) for v in variables],
                source_files=[dict(file=k,**entry) for k,entry in manifest['files'].items()],
                preview=[{'참여자 ID':str(int(row.SEQN)), '연령':'80세 이상' if row.RIDAGEYR==80 else int(row.RIDAGEYR),
                          '성별':'남성' if row.RIAGENDR==1 else '여성',
                          **{v['name']+' ('+v['unit']+')':None if pd.isna(row[v['variable']]) else float(row[v['variable']]) for v in variables}}
                         for _,row in joined.loc[joined['_merge'].eq('both')].head(8).iterrows()])
    return dataset, info


def make_plan(settings):
    if set(settings) != {'variables','age_group','by_sex','policy'}:
        raise ValueError('분석 설정에 알 수 없는 항목이 있습니다.')
    variables=settings['variables']
    if not isinstance(variables,list) or not variables or len(set(variables))!=len(variables) or not set(variables)<=BY_ID.keys():
        raise ValueError('분석할 검사항목을 하나 이상 선택해 주세요.')
    if settings['age_group'] not in AGE_GROUPS or type(settings['by_sex']) is not bool or settings['policy'] not in {'RELEASED','DELETE'}:
        raise ValueError('연령 범위·성별 비교·검출한계 처리 설정을 확인해 주세요.')
    age=AGE_GROUPS[settings['age_group']]
    condition=dict(COLUMN_ID='age',MIN=age['minimum'])
    if 'maximum_exclusive' in age:condition['MAX_EXCLUSIVE']=age['maximum_exclusive']
    groups={'SELECTED':dict(ALL_OF=[condition])}
    if settings['by_sex']:
        for label,sex in [('MALE','male'),('FEMALE','female')]:
            groups[label]=dict(ALL_OF=[deepcopy(condition),dict(COLUMN_ID='sex',IN_TEXT=[sex])])
    return dict(VERSION=1, MODE='SURVEY', RESULT_IDS=list(variables), POLICIES=[settings['policy']],
                PRIMARY_POLICY=settings['policy'], GROUPS=groups, PLOT_GROUP='SELECTED',
                PLOT_RESULT_IDS=list(variables), HISTOGRAM_BINS=20)


def run_analysis(dataset, settings):
    plan=make_plan(settings)
    return analyze_dataset(dataset,plan)
