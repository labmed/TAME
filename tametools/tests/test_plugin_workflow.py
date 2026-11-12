"""Connected plugin inputs, clinical grouping and reusable report regression tests."""
from copy import deepcopy
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import pytest

from tametools.io import read_tame, write_tame
from tametools.plugin_base import run_plugin
from tametools.plugin_workflow import PluginWorkflow, table_output
from tametools.provenance import log_entries
from tametools.provenance_audit import verify_history
from tametools.reporting import bind_report_outputs, chart_spec

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def source():
    return read_tame(ROOT / 'web/backend/examples/qc_workflow.tame')

def run(source, **options):
    cfg = {**source.meta['QC_WORKFLOW'], **options}
    return run_plugin(source, 'QC_WORKFLOW', source.meta, 'QC_WORKFLOW', cfg)

def test_branches_use_prepared_observations_and_preserve_input(source):
    before = deepcopy(source)
    result = run(source)
    pd.testing.assert_frame_equal(source.df, before.df)
    assert source.meta == before.meta
    tables = result.tables
    assert len(tables['precision.main']) == 8
    assert tables['precision.main']['n'].sum() == 47
    assert len(tables['rules.main']) == 48
    assert len(tables['review.main']) == 24
    assert tables['review.main'].not_evaluated.sum() == 1
    assert tables['trend.main']['count'].sum() == 47
    steps = {v['name']: v for v in result.dataset.meta['WORKFLOW_RESULT']['STEPS']}
    assert steps['precision']['source'] == steps['rules']['source'] == 'prepare'
    assert steps['trend']['source'] == steps['review']['source'] == 'rules'
    assert steps['rules']['options']['BASELINES'] == source.meta['QC_ANALYSIS']['BASELINES']
    assert len(result.charts) == 5
    assert {r['result_id'] for r in result.charts[1]['ROWS']} == {'glucose'}
    assert {r['result_id'] for r in result.charts[2]['ROWS']} == {'alt'}

def test_run_review_agrees_with_point_flags_and_keeps_unevaluated(source):
    result = run(source)
    rules, review = result.tables['rules.main'], result.table
    for _, row in review.iterrows():
        points = rules.loc[(rules.result_id == row.result_id) & (rules.run == row.run)]
        assert row.flagged == (~points.violation.isin(['NONE', 'NE'])).sum()
        assert row.not_evaluated == points.status.ne('evaluated').sum()
        assert row.review == ('REVIEW' if row.flagged or row.not_evaluated else 'NO_RULE_FLAG')
    assert any(rules.violation.str.contains('R_4s'))
    assert any(rules.violation.str.contains('2_2s'))
    assert any(rules.violation.str.contains('1_3s'))

def test_precision_separates_declared_levels_lots_and_analytes(source):
    result = run(source).tables['precision.main']
    for _, row in result.iterrows():
        column = next(c.name for c in source.columns if source.column_metadata(c)['ID'] == row.result_id)
        raw = source.df.loc[(source.df['관리수준'] == row.level) & (source.df['관리물질lot'] == row.lot), column]
        values = pd.to_numeric(raw, errors='coerce').dropna()
        assert row['n'] == len(values)
        assert row['mean'] == round(values.mean(), 4)
        assert row.sd == round(values.std(ddof=1), 4)
        assert row.cv_percent == round(100 * values.std(ddof=1) / abs(values.mean()), 3)

def test_singleton_sd_cv_are_undefined(source):
    small = source.replace(df=source.df.iloc[:1].copy())
    out = run_plugin(small, 'QC_ANALYSIS', small.meta, 'precision', {**small.meta['QC_ANALYSIS'], 'MODE':'PRECISION'})
    assert out.table['n'].eq(1).all()
    assert out.table.sd.isna().all() and out.table.cv_percent.isna().all()
    assert out.table.status.eq('insufficient_n').all()
    assert out.warnings

def test_histories_survive_branches_and_tame_roundtrip(source, tmp_path):
    output = run(source)
    operations = [e['OPERATION'] for e in log_entries(output.dataset)]
    for name in ['validate','prepare','precision','rules','trend','review']:
        assert 'WORKFLOW:'+name.upper() in operations
    assert verify_history(output.dataset)['checks']['history'] == 'PASS'
    path = tmp_path/'result.tame'
    write_tame(path, output.dataset, standardize_sex_values=False)
    replay = read_tame(path)
    checks = verify_history(replay)
    assert all(checks['checks'][key] == 'PASS' for key in ['history','data','controls']), checks
    charts = replay.meta['VISUALIZATIONS']
    assert charts
    assert any('ROWS' in chart for chart in charts.values())

def test_failure_stops_stage_and_retains_parent_history(source):
    flow = PluginWorkflow(source)
    flow.validate()
    with pytest.raises(ValueError) as error:
        flow.plugin('bad', 'QC_RUN_REVIEW', source='validate')
    assert list(flow.outputs) == ['validate']
    entries = log_entries(error.value.audit_dataset)
    assert entries[-1]['STATUS'] == 'FAILED'
    assert any(e['OPERATION'] == 'WORKFLOW:VALIDATE' for e in entries)
    with pytest.raises(ValueError, match='Unknown workflow source'):
        flow.plugin('later', 'QC_ANALYSIS', source='bad')

@pytest.mark.parametrize('name', ['INPUT', '../escape', 'two words', 'validate'])
def test_step_names_reject_unsafe_or_duplicate(source, name):
    flow = PluginWorkflow(source); flow.validate()
    with pytest.raises(ValueError, match='step names'):
        flow.validate(name)

def test_report_charts_bind_to_each_plugins_own_main_table():
    chart = chart_spec('same', x='label', y='value')
    outputs = [table_output('sample', pd.DataFrame({'label':['A'], 'value':[value]}), charts=[chart]) for value in [1, 100]]
    tables, charts = bind_report_outputs(outputs, names=['left','right'])
    assert set(tables) == {'left.main', 'right.main'}
    assert [c['ROWS'][0]['value'] for c in charts] == [1,100]
    assert [c['TABLE'] for c in charts] == ['left.main','right.main']
    outputs[0].charts[0]['TABLE'] = 'missing'
    with pytest.raises(ValueError, match='Unknown chart table'):
        bind_report_outputs(outputs)

def test_full_report_and_step_files_are_replayable(source, tmp_path):
    directory = tmp_path/'bundle'
    output = run(source, OUTPUT_DIR=str(directory), REPORT_MAX_ROWS='3')
    assert (directory/'report.docx').is_file()
    html = (directory/'report.html').read_text(encoding='utf-8')
    assert 'data:image/png;base64,' in html and '문서 표시 3행' in html
    assert len(pd.read_csv(directory/'tables/rules.main.csv')) == 48
    with zipfile.ZipFile(directory/'report.docx') as doc:
        assert len([n for n in doc.namelist() if n.startswith('word/media/')]) == 5
    with zipfile.ZipFile(directory/'report.zip') as archive:
        assert {'analysis_input.tame','result.tame','results/rules.tame','results/prepare.tame','workflow.json'} <= set(archive.namelist())
    restored = read_tame(directory/'analysis_input.tame')
    replay = run(restored)
    pd.testing.assert_frame_equal(output.table, replay.table)
    assert verify_history(read_tame(directory/'results/rules.tame'))['checks']['data'] == 'PASS'
    with pytest.raises(FileExistsError):
        run(source, OUTPUT_DIR=str(directory))


def test_generic_trend_does_not_pool_colliding_display_labels():
    frame = pd.DataFrame({'date':['2026-01-01']*4, 'value':[1.,3.,101.,103.],
        'left':['A / B']*2+['A']*2, 'right':['C']*2+['B / C']*2})
    ds = table_output('fixture', frame, result_columns=['value']).dataset
    out = run_plugin(ds, 'RESULT_TREND', ds.meta, 'trend', {'RESULT_IDS':['value'], 'DATE':'id:date', 'PERIOD':'D', 'GROUP_BY_IDS':['left','right']})
    assert sorted(out.table['mean']) == [2,102]
    assert out.table.test.nunique() == 2
    assert len(out.table[['group_left','group_right']].drop_duplicates()) == 2


def test_empty_bound_chart_preserves_schema_and_does_not_select_another_table(tmp_path):
    from tametools.reporting import write_docx_report
    empty = table_output('empty', pd.DataFrame({'label':pd.Series(dtype=str), 'value':pd.Series(dtype=float)}),
                         charts=[chart_spec('empty', x='label', y='value')])
    tables, charts = bind_report_outputs([empty])
    assert charts[0]['ROWS'] == []
    output = table_output('report', pd.DataFrame({'unrelated':[99]}), charts=charts)
    write_docx_report(tmp_path/'empty.docx', title='Empty chart', outputs=[output])
    assert (tmp_path/'empty.docx').exists()


def test_all_missing_precision_group_keeps_its_denominator(source):
    from tametools.cellstate import NULL
    source.df['혈당'] = NULL
    result = run_plugin(source, 'QC_ANALYSIS', source.meta, 'precision', {**source.meta['QC_ANALYSIS'], 'MODE':'PRECISION'})
    glucose = result.table.loc[result.table.result_id.eq('glucose')]
    assert len(glucose) == 4 and glucose.input_n.sum() == 24
    assert glucose['n'].eq(0).all() and glucose.missing_n.sum() == 24
    assert glucose.status.eq('no_numeric_results').all()
